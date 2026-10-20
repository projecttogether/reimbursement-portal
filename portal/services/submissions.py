import hashlib
import json
import secrets
import uuid
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac

from portal.forms import ClaimForm, parse_amount
from portal.models import Claim, Draft, Event, Expense, Job, Upload
from portal.services.storage import path_for


class SubmissionError(Exception):
    def __init__(self, message, *, errors=None, status=400):
        self.message, self.errors, self.status = message, errors or {}, status


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def session_hash(request, *, create=False):
    if not request.session.session_key:
        if not create:
            return None
        request.session.create()
    return salted_hmac("draft-session", request.session.session_key).hexdigest()


@transaction.atomic
def get_or_create_draft(request, event):
    binding = session_hash(request)
    current = request.session.get(f"draft:{event.slug}", {})
    draft = (
        Draft.objects.filter(pk=current.get("id"), event=event, session_hash=binding).first()
        if current.get("id")
        else None
    )
    if draft and draft.claim_id:
        return draft, current["token"]
    if draft and draft.expires_at > timezone.now() and draft.config_version == event.config_version:
        return draft, current["token"]
    if (
        Draft.objects.filter(claim__isnull=True, expires_at__gt=timezone.now()).count()
        >= settings.MAX_ACTIVE_DRAFTS
    ):
        raise SubmissionError(
            "Das Formular ist gerade ausgelastet. Bitte versuche es später erneut.", status=503
        )
    if (
        Draft.objects.filter(
            session_hash=binding, created_at__gt=timezone.now() - timedelta(days=1)
        ).count()
        >= 10
    ):
        raise SubmissionError(
            "Zu viele neue Formulare. Bitte versuche es später erneut.", status=429
        )
    token = secrets.token_urlsafe(32)
    draft = Draft.objects.create(
        event=event,
        session_hash=binding,
        token_hash=digest(token),
        config_version=event.config_version,
    )
    request.session[f"draft:{event.slug}"] = {"id": str(draft.id), "token": token}
    return draft, token


def authorize_draft(request, draft_id, token, event_slug, *, lock=False):
    try:
        draft_id = uuid.UUID(str(draft_id))
        query = Draft.objects.select_for_update() if lock else Draft.objects
        draft = query.get(pk=draft_id, event__slug=event_slug, session_hash=session_hash(request))
    except (Draft.DoesNotExist, ValueError, TypeError) as exc:
        raise SubmissionError("Das Formular wurde nicht gefunden.", status=404) from exc
    if not isinstance(token, str) or not constant_time_compare(draft.token_hash, digest(token)):
        raise SubmissionError("Das Formular wurde nicht gefunden.", status=404)
    if not draft.claim_id and draft.expires_at <= timezone.now():
        raise SubmissionError(
            "Das Formular ist abgelaufen. Bitte öffne das Formular erneut.", status=410
        )
    return draft


def normalized_payload(data, event):
    form = ClaimForm(data, event=event)
    if not form.is_valid():
        raise SubmissionError(
            "Bitte prüfe die markierten Angaben.", errors=form.errors.get_json_data()
        )
    expenses = data.get("expenses")
    if not isinstance(expenses, list) or not 1 <= len(expenses) <= settings.MAX_EXPENSES:
        raise SubmissionError("Bitte füge 1 bis 30 Auslagenpositionen hinzu.")
    cleaned, row_ids, all_upload_ids, totals = [], set(), set(), {}
    for position, row in enumerate(expenses):
        try:
            if not isinstance(row, dict):
                raise ValueError("Ungültige Position.")
            row_id = str(uuid.UUID(row["id"]))
            if row_id in row_ids:
                raise ValueError("Positionen müssen unterschiedliche Kennungen haben.")
            row_ids.add(row_id)
            item = {"id": row_id}
            for key, maximum in [("merchant", 200), ("purpose", 500), ("receipt_reference", 100)]:
                value = row.get(key)
                if not isinstance(value, str) or not 1 <= len(value.strip()) <= maximum:
                    raise ValueError(
                        "Geschäftspartner, Anlass und Belegreferenz sind erforderlich."
                    )
                item[key] = value.strip()
            currency = row.get("currency")
            if currency not in event.allowed_currencies:
                raise ValueError("Diese Währung ist für das Formular nicht freigegeben.")
            amount = parse_amount(row.get("amount"), currency)
            ids = row.get("uploads")
            if not isinstance(ids, list) or not 1 <= len(ids) <= settings.MAX_FILES_PER_EXPENSE:
                raise ValueError("Jede Position benötigt 1 bis 3 Belegdateien.")
            ids = [str(uuid.UUID(value)) for value in ids]
            if len(set(ids)) != len(ids) or all_upload_ids.intersection(ids):
                raise ValueError("Eine Belegdatei darf nur einmal zugeordnet werden.")
            all_upload_ids.update(ids)
            item.update(
                {"currency": currency, "amount": str(amount), "uploads": ids, "position": position}
            )
            totals[currency] = totals.get(currency, Decimal(0)) + amount
            cleaned.append(item)
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise SubmissionError(f"Position {position + 1}: {exc}") from exc
    payload = {
        key: value.isoformat() if hasattr(value, "isoformat") else value
        for key, value in form.cleaned_data.items()
    }
    try:
        payload["signature"] = str(uuid.UUID(data["signature"]))
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        raise SubmissionError("Bitte zeichne oder lade eine Unterschrift hoch.") from exc
    payload["expenses"] = cleaned
    payload["config_version"] = data.get("config_version")
    return payload, {currency: str(amount) for currency, amount in totals.items()}


@transaction.atomic
def submit_claim(request, event_slug, data):
    if not isinstance(data, dict) or data.get("website"):
        raise SubmissionError("Ungültige Anfrage.")
    draft = authorize_draft(request, data.get("draft_id"), data.get("token"), event_slug, lock=True)
    event = Event.objects.select_for_update().get(pk=draft.event_id)
    # Compare an exact canonical request on replay, even if the event changed or closed later.
    replay_data = {key: value for key, value in data.items() if key not in {"token", "website"}}
    payload_hash = digest(
        json.dumps(replay_data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    )
    if draft.claim_id:
        if not constant_time_compare(draft.payload_hash, payload_hash):
            raise SubmissionError(
                "Dieses Formular wurde bereits mit anderen Angaben eingereicht.", status=409
            )
        return draft.claim
    if not event.is_open():
        raise SubmissionError("Dieses Formular ist für neue Anträge geschlossen.", status=409)
    if (
        data.get("config_version") != draft.config_version
        or event.config_version != draft.config_version
    ):
        raise SubmissionError(
            "Das Formular wurde aktualisiert. Bitte lade es neu und prüfe die Angaben erneut.",
            status=409,
        )
    if event.recipient.lower() not in settings.APPROVED_DELIVERY_RECIPIENTS:
        raise SubmissionError("Das Formular ist derzeit nicht verfügbar.", status=503)
    payload, totals = normalized_payload(data, event)
    all_ids = [file_id for row in payload["expenses"] for file_id in row["uploads"]] + [
        payload["signature"]
    ]
    files = {
        str(upload.id): upload
        for upload in Upload.objects.select_for_update().filter(
            draft=draft, id__in=all_ids, claim__isnull=True
        )
    }
    if len(files) != len(all_ids) or any(
        upload.state != Upload.State.READY for upload in files.values()
    ):
        raise SubmissionError("Bitte warte, bis alle Belege und die Unterschrift geprüft sind.")
    signature = files[payload["signature"]]
    if signature.kind != "signature" or any(
        not path_for(upload.normalized_key).is_file() for upload in files.values()
    ):
        raise SubmissionError("Eine Datei ist nicht verfügbar. Bitte lade sie erneut hoch.")
    if (
        sum(upload.size for upload in files.values() if upload.kind == "receipt")
        > settings.MAX_SUBMISSION_BYTES
        or sum(upload.pages for upload in files.values() if upload.kind == "receipt")
        > settings.MAX_CLAIM_PAGES
    ):
        raise SubmissionError("Die Belege überschreiten 50 MB oder 100 Seiten.")
    if Expense.objects.filter(id__in=[row["id"] for row in payload["expenses"]]).exists():
        raise SubmissionError(
            "Ungültige Positionskennung. Bitte öffne ein neues Formular.", status=409
        )
    fields = {
        key: value
        for key, value in payload.items()
        if key
        not in {"signature", "expenses", "declaration", "privacy_acknowledged", "config_version"}
    }
    claim = Claim.objects.create(
        event=event,
        snapshot=event.snapshot(),
        totals=totals,
        resolution_due=timezone.now() + timedelta(days=30),
        **{
            key: value or None if key in {"trip_start", "trip_end"} else value
            for key, value in fields.items()
        },
    )
    for row in payload["expenses"]:
        for position, file_id in enumerate(row["uploads"]):
            upload = files[file_id]
            if upload.kind != "receipt" or str(upload.expense_id) != row["id"]:
                raise SubmissionError("Ein Beleg gehört nicht zu dieser Auslagenposition.")
            upload.claim, upload.position = claim, position
            upload.save(update_fields=["claim", "position"])
        Expense.objects.create(
            claim=claim, **{key: value for key, value in row.items() if key != "uploads"}
        )
    signature.claim = claim
    signature.save(update_fields=["claim"])
    draft.claim, draft.payload_hash = claim, payload_hash
    draft.expires_at = timezone.now() + timedelta(days=7)
    draft.save(update_fields=["claim", "payload_hash", "expires_at"])
    Job.objects.create(key=f"render:{claim.id}", kind="render", claim=claim)
    return claim


def draft_receipt_bytes(draft):
    return draft.uploads.filter(kind="receipt").aggregate(total=Sum("size"))["total"] or 0
