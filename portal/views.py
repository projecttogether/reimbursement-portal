import json
import secrets
from functools import wraps

from django.conf import settings
from django.db import connection
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from portal import intake
from portal.forms import SEPA_COUNTRIES, ClaimForm
from portal.intake import IntakeError
from portal.models import Claim, Form, PrivacyNotice
from portal.roles import is_form_editor, is_template_admin
from portal.services import delivery, uploads
from portal.services.capacity import storage_available
from portal.services.drafts import get_or_create_draft, session_hash
from portal.services.mailersend import verify_webhook
from portal.services.readiness import dependencies
from portal.services.storage import check_writable
from portal.services.submissions import submit_claim


def api_errors(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        try:
            return view(request, *args, **kwargs)
        except IntakeError as exc:
            return JsonResponse({"error": exc.message, "fields": exc.errors}, status=exc.status)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JsonResponse({"error": "Ungültige Anfrage."}, status=400)

    return wrapped


def can_preview(user, form):
    return is_form_editor(user) and (
        is_template_admin(user) or form.owners.filter(pk=user.pk).exists()
    )


@require_GET
def index(request):
    return render(request, "portal/index.html")


@require_GET
def privacy(request):
    return render(request, "portal/privacy.html", {"notice": PrivacyNotice.current()})


@require_GET
@ensure_csrf_cookie
def form_page(request, form_slug):
    form = get_object_or_404(Form.objects.select_related("template"), slug=form_slug)
    # Owners can look at a form before it opens; nothing can be uploaded or submitted.
    preview = not form.is_open() and can_preview(request.user, form)
    if not form.is_open() and not preview:
        return render(request, "portal/closed.html", {"form": form}, status=403)
    notice = PrivacyNotice.current()
    if notice is None:
        return render(
            request,
            "portal/closed.html",
            {"form": form, "message": "Das Formular ist derzeit nicht verfügbar."},
            status=503,
        )
    draft, token = None, ""
    if not preview:
        try:
            draft, token = get_or_create_draft(request, form)
        except IntakeError as exc:
            return render(
                request,
                "portal/closed.html",
                {"form": form, "message": exc.message},
                status=exc.status,
            )
    template = form.template
    claim_form = ClaimForm(
        form=form, initial={"trip_start": form.trip_start, "trip_end": form.trip_end}
    )
    configuration = {
        "draft_id": str(draft.id) if draft else "",
        "token": token,
        "config_version": draft.config_version if draft else form.config_version,
        "preview": preview,
        "sepa_countries": sorted(SEPA_COUNTRIES),
        "travel": template.travel,
        "mileage_rate": str(template.mileage_rate) if template.mileage_enabled else None,
        "purpose_options": bool(form.purpose_choices()),
        "upload_url": reverse("upload", kwargs={"form_slug": form.slug}),
        "submit_url": reverse("submit", kwargs={"form_slug": form.slug}),
        **intake.client_config(),
    }
    return render(
        request,
        "portal/form.html",
        {
            "form": form,
            "template": template,
            "claim_form": claim_form,
            "privacy_notice": notice,
            "configuration": configuration,
            "intake_messages": intake.messages(),
            "amount_pattern": intake.AMOUNT_PATTERN,
            "field_lengths": intake.EXPENSE_FIELD_LENGTHS,
            "max_distance_km": intake.MAX_DISTANCE_KM,
            "development": settings.DEBUG,
            "preview": preview,
        },
    )


@require_POST
@api_errors
def upload(request, form_slug):
    if not storage_available():
        raise IntakeError("Der Upload ist vorübergehend nicht verfügbar.", status=503)
    # Parsing the body sets the flag when the bounded upload handler stopped it.
    file = request.FILES.get("file")
    if getattr(request, "upload_limit_exceeded", False):
        raise IntakeError(intake.message("receipt_too_large"), status=413)
    item = uploads.accept(
        request,
        form_slug,
        file=file,
        kind=request.POST.get("kind", "receipt"),
        expense_id=request.POST.get("expense_id"),
        draft_id=request.POST.get("draft_id"),
        token=request.POST.get("token"),
    )
    return JsonResponse(
        {
            "id": str(item.id),
            "state": item.state,
            "name": item.original_name,
            "size": item.size,
            "status_url": reverse(
                "upload_status", kwargs={"form_slug": form_slug, "upload_id": item.id}
            ),
            "delete_url": reverse(
                "upload_remove", kwargs={"form_slug": form_slug, "upload_id": item.id}
            ),
        },
        status=202,
    )


@require_GET
def upload_status(request, form_slug, upload_id):
    return JsonResponse(uploads.status(uploads.owned(request, form_slug, upload_id)))


@require_http_methods(["DELETE"])
@api_errors
def upload_remove(request, form_slug, upload_id):
    uploads.discard(request, form_slug, upload_id)
    return JsonResponse({"removed": True})


@require_POST
@api_errors
def submit(request, form_slug):
    data = json.loads(request.body)
    claim = submit_claim(request, form_slug, data)
    return JsonResponse(
        {
            "reference": claim.reference,
            "receipt_url": reverse("received", kwargs={"claim_id": claim.id}),
        },
        status=202,
    )


@require_GET
def received(request, claim_id):
    claim = get_object_or_404(
        Claim.objects.select_related("form"),
        pk=claim_id,
        accepted_draft__session_hash=session_hash(request),
    )
    status, final = delivery.participant_status(claim)
    if request.GET.get("format") == "json":
        return JsonResponse({"reference": claim.reference, "state": status, "final": final})
    return render(
        request,
        "portal/received.html",
        {"claim": claim, "status": status, "final": final, "form": claim.form},
    )


@csrf_exempt
@require_POST
def mailersend_webhook(request):
    try:
        raw = request.body
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError
        test = data.get("type") == "webhook.test"
        if not verify_webhook(raw, request.headers.get("Signature", ""), test=test):
            return JsonResponse({"error": "invalid_signature"}, status=401)
        if not test:
            delivery.ingest_event(data)
    except (ValueError, TypeError, AttributeError, KeyError):
        return JsonResponse({"error": "invalid_event"}, status=400)
    return JsonResponse({"received": True})


def health_authorized(request):
    return settings.DEBUG or bool(
        settings.HEALTHCHECK_TOKEN
        and secrets.compare_digest(
            request.headers.get("X-Health-Token", ""), settings.HEALTHCHECK_TOKEN
        )
    )


@require_GET
def healthz(request):
    if not health_authorized(request):
        raise Http404
    return JsonResponse({"status": "OK", "timestamp": timezone.now().isoformat()})


@require_GET
def readyz(request):
    if not health_authorized(request):
        raise Http404
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        if not storage_available():
            raise OSError
        check_writable()
        checks, age = dependencies()
        if not all(checks.values()):
            return JsonResponse(
                {"status": "unavailable", "checks": checks, "oldest_job_age_seconds": age},
                status=503,
            )
    except Exception:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "OK", "checks": checks, "oldest_job_age_seconds": age})
