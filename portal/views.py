import json
import secrets
import uuid
from functools import wraps
from pathlib import Path

from django.conf import settings
from django.db import connection, transaction
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from portal.forms import ClaimForm
from portal.models import Claim, Draft, Event, Job, Upload, WorkspaceReservation
from portal.services.capacity import CapacityError, admit_upload
from portal.services.delivery import ingest_event
from portal.services.mailersend import verify_webhook
from portal.services.readiness import dependencies
from portal.services.storage import path_for, remove_keys, save_upload, storage_available
from portal.services.submissions import (
    SubmissionError,
    authorize_draft,
    draft_receipt_bytes,
    get_or_create_draft,
    session_hash,
    submit_claim,
)

UPLOAD_ERRORS = {
    "normalized_size_limit": "Die verarbeitete Datei ist zu groß. Bitte exportiere sie mit geringerer Auflösung.",
    "document_sandbox_unavailable": "Die Sicherheitsprüfung ist vorübergehend nicht verfügbar. Bitte kontaktiere uns.",
    "unsupported_type": "Bitte lade eine PDF-, JPG- oder PNG-Datei hoch.",
    "invalid_document": "Die Datei konnte nicht gelesen werden. Bitte exportiere sie erneut.",
    "encrypted_pdf": "Die PDF ist passwortgeschützt. Bitte lade eine ungeschützte Kopie hoch.",
    "pdf_pages": "Eine PDF darf höchstens 30 Seiten enthalten.",
    "pdf_active_content": "Diese PDF enthält nicht unterstützte aktive Inhalte. Bitte exportiere sie als einfache PDF.",
    "empty_signature": "Die Unterschrift ist leer. Bitte unterschreibe erneut.",
    "malware_detected": "Die Datei hat die Sicherheitsprüfung nicht bestanden.",
    "scanner_unavailable": "Die Sicherheitsprüfung ist verzögert. Bitte warte oder versuche es später erneut.",
}


def api_errors(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        try:
            return view(request, *args, **kwargs)
        except SubmissionError as exc:
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
    except SubmissionError as exc:
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
        "max_file_bytes": settings.MAX_RECEIPT_BYTES,
        "max_submission_bytes": settings.MAX_SUBMISSION_BYTES,
        "max_expenses": settings.MAX_EXPENSES,
    }
    return render(
        request,
        "portal/form.html",
        {
            "event": event,
            "form": form,
            "configuration": configuration,
            "development": settings.DEBUG,
        },
    )


@require_POST
@api_errors
def upload(request, event_slug):
    if not storage_available():
        raise SubmissionError("Der Upload ist vorübergehend nicht verfügbar.", status=503)
    files = request.FILES
    if getattr(request, "upload_limit_exceeded", False):
        raise SubmissionError("Eine Belegdatei darf höchstens 10 MB groß sein.", status=413)
    file = files.get("file")
    kind = request.POST.get("kind", "receipt")
    if not file or kind not in {"receipt", "signature"}:
        raise SubmissionError("Bitte wähle eine Belegdatei oder Unterschrift aus.")
    extension = Path(file.name).suffix.lower()
    if extension not in (
        {".png", ".jpg", ".jpeg"} if kind == "signature" else {".pdf", ".png", ".jpg", ".jpeg"}
    ):
        raise SubmissionError(UPLOAD_ERRORS["unsupported_type"])
    try:
        expense_id = uuid.UUID(request.POST["expense_id"]) if kind == "receipt" else None
    except (KeyError, ValueError, TypeError) as exc:
        raise SubmissionError("Die Auslagenposition fehlt.") from exc
    key = ""
    try:
        with transaction.atomic():
            draft = authorize_draft(
                request,
                request.POST.get("draft_id"),
                request.POST.get("token"),
                event_slug,
                lock=True,
            )
            event = Event.objects.get(pk=draft.event_id)
            if (
                draft.claim_id
                or not event.is_open()
                or event.config_version != draft.config_version
            ):
                raise SubmissionError(
                    "Dieses Formular wurde bereits eingereicht, geschlossen oder aktualisiert.",
                    status=409,
                )
            count = draft.uploads.filter(kind=kind, expense_id=expense_id).count()
            if (
                count >= (settings.MAX_FILES_PER_EXPENSE if kind == "receipt" else 3)
                or draft.uploads.count()
                >= settings.MAX_EXPENSES * settings.MAX_FILES_PER_EXPENSE + 3
            ):
                raise SubmissionError("Bitte entferne zuerst eine vorhandene Datei.")
            if (
                kind == "receipt"
                and draft_receipt_bytes(draft) + file.size > settings.MAX_SUBMISSION_BYTES
            ):
                raise SubmissionError(
                    "Alle Belege zusammen dürfen höchstens 50 MB groß sein.", status=413
                )
            upload_id = uuid.uuid4()
            try:
                reservation = admit_upload(request, draft, file.size)
            except CapacityError as exc:
                messages = {
                    "draft_work_limit": "Für dieses Formular wurden zu viele Dateien hochgeladen. Bitte kontaktiere uns.",
                    "network_work_limit": "Zu viele Uploads aus diesem Netzwerk. Bitte versuche es später erneut.",
                    "draft_storage_limit": "Die Dateien benötigen zu viel Speicher. Bitte entferne nicht benötigte Dateien.",
                }
                raise SubmissionError(
                    messages.get(
                        str(exc),
                        "Die Upload-Kapazität ist gerade begrenzt. Bitte warte kurz und versuche es erneut.",
                    ),
                    status=429 if str(exc) in {"draft_work_limit", "network_work_limit"} else 503,
                ) from exc
            key = f"uploads/{upload_id}/source{extension}"
            try:
                size, checksum = save_upload(
                    file,
                    key,
                    settings.MAX_SIGNATURE_BYTES
                    if kind == "signature"
                    else settings.MAX_RECEIPT_BYTES,
                )
            except ValueError as exc:
                raise SubmissionError(
                    "Die Unterschrift darf höchstens 1 MB, ein Beleg höchstens 10 MB groß sein.",
                    status=413,
                ) from exc
            if not size:
                raise SubmissionError("Die Datei ist leer.")
            item = Upload.objects.create(
                id=upload_id,
                draft=draft,
                expense_id=expense_id,
                kind=kind,
                original_name=Path(file.name).name[:200],
                source_key=key,
                size=size,
                checksum=checksum,
                reserved_bytes=reservation,
            )
            Job.objects.create(key=f"validate:{item.id}", kind="validate", upload=item)
    except Exception:
        if key:
            remove_keys(key)
        raise
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


def owned_upload(request, event_slug, upload_id):
    return get_object_or_404(
        Upload,
        pk=upload_id,
        draft__event__slug=event_slug,
        draft__session_hash=session_hash(request),
        draft__expires_at__gt=timezone.now(),
    )


@require_GET
def upload_status(request, event_slug, upload_id):
    item = owned_upload(request, event_slug, upload_id)
    return JsonResponse(
        {
            "id": str(item.id),
            "state": item.state,
            "pages": item.pages,
            "message": UPLOAD_ERRORS.get(
                item.error_code,
                "Die Datei konnte nicht geprüft werden. Bitte lade sie erneut hoch.",
            )
            if item.error_code
            else "",
        }
    )


@require_http_methods(["DELETE"])
@api_errors
@transaction.atomic
def upload_remove(request, event_slug, upload_id):
    item = owned_upload(request, event_slug, upload_id)
    draft = Draft.objects.select_for_update().get(pk=item.draft_id)
    if draft.claim_id or item.claim_id:
        raise SubmissionError("Eingereichte Belege können nicht geändert werden.", status=409)
    jobs = list(item.job_set.select_for_update())
    if (
        any(job.state == "running" for job in jobs)
        or WorkspaceReservation.objects.filter(upload=item).exists()
    ):
        raise SubmissionError(
            "Die Datei wird gerade geprüft. Bitte versuche es kurz danach erneut.", status=409
        )
    remove_keys(item.source_key, item.normalized_key)
    item.delete()
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
        probe = path_for(f"checks/{uuid.uuid4()}")
        probe.parent.mkdir(exist_ok=True, mode=0o700)
        probe.write_bytes(b"ready")
        probe.unlink()
        checks, age = dependencies()
        if not all(checks.values()):
            return JsonResponse(
                {"status": "unavailable", "checks": checks, "oldest_job_age_seconds": age},
                status=503,
            )
    except Exception:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "OK", "checks": checks, "oldest_job_age_seconds": age})
