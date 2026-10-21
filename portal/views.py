import json
import secrets
from functools import wraps

from django.conf import settings
from django.db import connection
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from portal import intake
from portal.forms import ClaimForm
from portal.intake import IntakeError
from portal.models import Claim, Event
from portal.services import uploads
from portal.services.capacity import storage_available
from portal.services.delivery import ingest_event
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


@require_GET
def index(request):
    events = [event for event in Event.objects.filter(active=True) if event.is_open()]
    if len(events) == 1:
        return redirect("event_form", event_slug=events[0].slug)
    return render(request, "portal/index.html", {"events": events})


@require_GET
@ensure_csrf_cookie
def event_form(request, event_slug):
    event = get_object_or_404(Event, slug=event_slug)
    if not event.is_open():
        return render(request, "portal/closed.html", {"event": event}, status=403)
    if request.GET.get("new") == "1":
        request.session.pop(f"draft:{event.slug}", None)
    try:
        draft, token = get_or_create_draft(request, event)
    except IntakeError as exc:
        return render(
            request,
            "portal/closed.html",
            {"event": event, "message": exc.message},
            status=exc.status,
        )
    if draft.claim_id:
        return redirect("received", claim_id=draft.claim_id)
    form = ClaimForm(
        event=event, initial={"trip_start": event.trip_start, "trip_end": event.trip_end}
    )
    configuration = {
        "draft_id": str(draft.id),
        "token": token,
        "config_version": draft.config_version,
        "currencies": event.allowed_currencies,
        "upload_url": reverse("upload", kwargs={"event_slug": event.slug}),
        "submit_url": reverse("submit", kwargs={"event_slug": event.slug}),
        **intake.client_config(event.allowed_currencies),
    }
    return render(
        request,
        "portal/form.html",
        {
            "event": event,
            "form": form,
            "configuration": configuration,
            "intake_messages": intake.messages(),
            "amount_pattern": intake.AMOUNT_PATTERN,
            "field_lengths": intake.EXPENSE_FIELD_LENGTHS,
            "development": settings.DEBUG,
        },
    )


@require_POST
@api_errors
def upload(request, event_slug):
    if not storage_available():
        raise IntakeError("Der Upload ist vorübergehend nicht verfügbar.", status=503)
    # Parsing the body sets the flag when the bounded upload handler stopped it.
    file = request.FILES.get("file")
    if getattr(request, "upload_limit_exceeded", False):
        raise IntakeError(intake.message("receipt_too_large"), status=413)
    item = uploads.accept(
        request,
        event_slug,
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
                "upload_status", kwargs={"event_slug": event_slug, "upload_id": item.id}
            ),
            "delete_url": reverse(
                "upload_remove", kwargs={"event_slug": event_slug, "upload_id": item.id}
            ),
        },
        status=202,
    )


@require_GET
def upload_status(request, event_slug, upload_id):
    return JsonResponse(uploads.status(uploads.owned(request, event_slug, upload_id)))


@require_http_methods(["DELETE"])
@api_errors
def upload_remove(request, event_slug, upload_id):
    uploads.discard(request, event_slug, upload_id)
    return JsonResponse({"removed": True})


@require_POST
@api_errors
def submit(request, event_slug):
    data = json.loads(request.body)
    claim = submit_claim(request, event_slug, data)
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
        Claim, pk=claim_id, accepted_draft__session_hash=session_hash(request)
    )
    delivery = getattr(claim, "delivery", None)
    status = delivery.state if delivery else claim.state
    if request.GET.get("format") == "json":
        return JsonResponse({"reference": claim.reference, "state": status})
    return render(
        request, "portal/received.html", {"claim": claim, "status": status, "event": claim.event}
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
            ingest_event(data)
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
