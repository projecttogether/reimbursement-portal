import json
import uuid
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import constant_time_compare

from portal.forms import ClaimForm
from portal.intake import EXPENSE_FIELD_LENGTHS, IntakeError, message, parse_amount
from portal.models import Claim, Event, Expense
from portal.services import queue, uploads
from portal.services.delivery import recipient_approved
from portal.services.drafts import authorize_draft, digest, ensure_accepting


def normalized_payload(data, event):
    form = ClaimForm(data, event=event)
    if not form.is_valid():
        raise IntakeError("Bitte prüfe die markierten Angaben.", errors=form.errors.get_json_data())
    expenses = data.get("expenses")
    if not isinstance(expenses, list) or not 1 <= len(expenses) <= settings.MAX_EXPENSES:
        raise IntakeError(message("expense_count"))
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
            for key, maximum in EXPENSE_FIELD_LENGTHS.items():
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
                raise ValueError(message("files_per_expense"))
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
            raise IntakeError(f"Position {position + 1}: {exc}") from exc
    payload = {
        key: value.isoformat() if hasattr(value, "isoformat") else value
        for key, value in form.cleaned_data.items()
    }
    try:
        payload["signature"] = str(uuid.UUID(data["signature"]))
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        raise IntakeError("Bitte zeichne oder lade eine Unterschrift hoch.") from exc
    payload["expenses"] = cleaned
    payload["config_version"] = data.get("config_version")
    return payload, {currency: str(amount) for currency, amount in totals.items()}


@transaction.atomic
def submit_claim(request, event_slug, data):
    if not isinstance(data, dict) or data.get("website"):
        raise IntakeError("Ungültige Anfrage.")
    draft = authorize_draft(request, data.get("draft_id"), data.get("token"), event_slug, lock=True)
    event = Event.objects.select_for_update().get(pk=draft.event_id)
    # Compare an exact canonical request on replay, even if the event changed or closed later.
    replay_data = {key: value for key, value in data.items() if key not in {"token", "website"}}
    payload_hash = digest(
        json.dumps(replay_data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    )
    if draft.claim_id:
        if not constant_time_compare(draft.payload_hash, payload_hash):
            raise IntakeError(
                "Dieses Formular wurde bereits mit anderen Angaben eingereicht.", status=409
            )
        return draft.claim
    ensure_accepting(draft, event, form_version=data.get("config_version"))
    if not recipient_approved(event.recipient):
        raise IntakeError("Das Formular ist derzeit nicht verfügbar.", status=503)
    payload, totals = normalized_payload(data, event)
    all_ids = [file_id for row in payload["expenses"] for file_id in row["uploads"]] + [
        payload["signature"]
    ]
    files = uploads.claimable(draft, all_ids)
    signature = files[payload["signature"]]
    if signature.kind != "signature":
        raise IntakeError("Eine Datei ist nicht verfügbar. Bitte lade sie erneut hoch.")
    if (
        sum(upload.size for upload in files.values() if upload.kind == "receipt")
        > settings.MAX_SUBMISSION_BYTES
        or sum(upload.pages for upload in files.values() if upload.kind == "receipt")
        > settings.MAX_CLAIM_PAGES
    ):
        raise IntakeError(message("claim_too_large"))
    if Expense.objects.filter(id__in=[row["id"] for row in payload["expenses"]]).exists():
        raise IntakeError("Ungültige Positionskennung. Bitte öffne ein neues Formular.", status=409)
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
            uploads.attach(files[file_id], claim, expense_id=row["id"], position=position)
        Expense.objects.create(
            claim=claim, **{key: value for key, value in row.items() if key != "uploads"}
        )
    uploads.attach(signature, claim)
    draft.claim, draft.payload_hash = claim, payload_hash
    draft.expires_at = timezone.now() + timedelta(days=7)
    draft.save(update_fields=["claim", "payload_hash", "expires_at"])
    queue.enqueue("render", claim)
    return claim
