import json
import uuid
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.utils.dateparse import parse_date

from portal.forms import ClaimForm
from portal.intake import (
    EXPENSE_FIELD_LENGTHS,
    MAX_CLAIM_TOTAL,
    IntakeError,
    message,
    mileage_amount,
    parse_amount,
    parse_distance,
)
from portal.models import Claim, Expense
from portal.services import drafts, queue, uploads
from portal.services.drafts import digest
from portal.services.references import allocate_reference


def _text(row, key, *, required=True):
    value = row.get(key, "")
    if not isinstance(value, str) or len(value.strip()) > EXPENSE_FIELD_LENGTHS[key]:
        raise ValueError("Ungültige Angabe.")
    value = value.strip()
    if required and not value:
        raise ValueError(REQUIRED_MESSAGES[key])
    return value


REQUIRED_MESSAGES = {
    "merchant": "Bitte gib den Geschäftspartner an.",
    "vehicle_registration": "Bitte gib das Kennzeichen des Fahrzeugs an.",
    "route_from": "Bitte gib den Startort der Fahrt an.",
    "route_to": "Bitte gib das Ziel der Fahrt an.",
}


def _expense(row, template, purpose):
    """One cleaned expense row without its uploads, or ValueError."""
    kind = row.get("kind", Expense.Kind.RECEIPT)
    item = {"kind": kind, "purpose": purpose}
    if kind == Expense.Kind.RECEIPT:
        item["merchant"] = _text(row, "merchant")
        item["receipt_reference"] = _text(row, "receipt_reference", required=False)
        item["amount"] = parse_amount(row.get("amount"))
        return item, 1
    if kind != Expense.Kind.MILEAGE or not template.mileage_enabled:
        raise ValueError("Diese Art von Position ist für das Formular nicht freigegeben.")
    try:
        travel_date = parse_date(row.get("travel_date") or "")
    except (TypeError, ValueError):
        travel_date = None
    if not travel_date:
        raise ValueError("Bitte gib das Datum der Fahrt an.")
    round_trip = row.get("round_trip", False)
    if not isinstance(round_trip, bool):
        raise ValueError("Ungültige Angabe zu Hin- und Rückfahrt.")
    return_date = None
    if round_trip:
        try:
            return_date = parse_date(row.get("return_date") or "")
        except (TypeError, ValueError):
            pass
        if not return_date:
            raise ValueError("Bitte gib das Rückfahrtdatum an.")
        if return_date < travel_date:
            raise ValueError("Das Rückfahrtdatum darf nicht vor der Hinfahrt liegen.")
    distance = parse_distance(row.get("distance_km"))
    registration = _text(row, "vehicle_registration").upper()
    if len(registration) > EXPENSE_FIELD_LENGTHS["vehicle_registration"]:
        raise ValueError("Das Kennzeichen darf höchstens 20 Zeichen haben.")
    item.update(
        {
            "merchant": "",
            "receipt_reference": "",
            "travel_date": travel_date,
            "return_date": return_date,
            "vehicle_registration": registration,
            "route_from": _text(row, "route_from"),
            "route_to": _text(row, "route_to"),
            "distance_km": distance,
            "round_trip": round_trip,
            "mileage_rate": template.mileage_rate,
            "amount": mileage_amount(distance, round_trip, template.mileage_rate),
        }
    )
    return item, 0


def normalized_payload(data, form):
    expenses = data.get("expenses")
    if not isinstance(expenses, list) or not 1 <= len(expenses) <= settings.MAX_EXPENSES:
        raise IntakeError(message("expense_count"))
    has_mileage = any(
        isinstance(row, dict) and row.get("kind") == Expense.Kind.MILEAGE for row in expenses
    )
    claim_form = ClaimForm(data, form=form, has_mileage=has_mileage)
    if not claim_form.is_valid():
        raise IntakeError(
            "Bitte prüfe die markierten Angaben.", errors=claim_form.errors.get_json_data()
        )
    cleaned, row_ids, all_upload_ids, total = [], set(), set(), Decimal(0)
    for position, row in enumerate(expenses):
        try:
            if not isinstance(row, dict):
                raise ValueError("Ungültige Position.")
            row_id = str(uuid.UUID(row["id"]))
            if row_id in row_ids:
                raise ValueError("Positionen müssen unterschiedliche Kennungen haben.")
            row_ids.add(row_id)
            item, minimum_files = _expense(row, form.template, claim_form.cleaned_data["purpose"])
            ids = row.get("uploads")
            if (
                not isinstance(ids, list)
                or not minimum_files <= len(ids) <= settings.MAX_FILES_PER_EXPENSE
            ):
                raise ValueError(message("files_per_expense" if minimum_files else "mileage_files"))
            ids = [str(uuid.UUID(value)) for value in ids]
            if len(set(ids)) != len(ids) or all_upload_ids.intersection(ids):
                raise ValueError("Eine Belegdatei darf nur einmal zugeordnet werden.")
            all_upload_ids.update(ids)
            item.update({"id": row_id, "uploads": ids, "position": position})
            total += item["amount"]
            cleaned.append(item)
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise IntakeError(f"Position {position + 1}: {exc}") from exc
    if total > MAX_CLAIM_TOTAL:
        raise IntakeError("Die Summe aller Positionen ist zu hoch. Bitte prüfe die Beträge.")
    payload = dict(claim_form.cleaned_data)
    try:
        payload["signature"] = str(uuid.UUID(data["signature"]))
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        raise IntakeError("Bitte zeichne oder lade eine Unterschrift hoch.") from exc
    payload["expenses"] = cleaned
    return payload, total


@transaction.atomic
def submit_claim(participant, form_slug, data):
    """Accept the participant's draft as a claim; a repeated identical request returns it."""
    if not isinstance(data, dict) or data.get("website"):
        raise IntakeError("Ungültige Anfrage.")
    # Compare an exact canonical request on replay, even if the form changed or closed later.
    replay_data = {key: value for key, value in data.items() if key not in {"token", "website"}}
    payload_hash = digest(
        json.dumps(replay_data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    )
    try:
        draft, form = drafts.gate(
            participant, form_slug, data.get("draft_id"), data.get("token"), drafts.SUBMIT
        )
    except drafts.AlreadySubmitted as submitted:
        if not constant_time_compare(submitted.draft.payload_hash, payload_hash):
            raise IntakeError(
                "Dieses Formular wurde bereits mit anderen Angaben eingereicht.", status=409
            ) from None
        return submitted.draft.claim
    # A privacy notice published since the gate's check must not enter this claim.
    snapshot = form.snapshot()
    if snapshot["revision"] != draft.revision:
        raise drafts.Outdated()
    payload, total = normalized_payload(data, form)
    all_ids = [file_id for row in payload["expenses"] for file_id in row["uploads"]] + [
        payload["signature"]
    ]
    files = uploads.claimable(draft, all_ids)
    signature = files[payload["signature"]]
    if signature.kind != "signature":
        raise IntakeError("Eine Datei ist nicht verfügbar. Bitte lade sie erneut hoch.")
    if Expense.objects.filter(id__in=[row["id"] for row in payload["expenses"]]).exists():
        raise IntakeError("Ungültige Positionskennung. Bitte öffne ein neues Formular.", status=409)
    fields = {
        key: value
        for key, value in payload.items()
        if key not in {"signature", "expenses", "declaration", "privacy_acknowledged", "is_trip"}
    }
    submitted_at = timezone.now()
    claim = Claim.objects.create(
        form=form,
        submitted_at=submitted_at,
        bookkeeping_reference=allocate_reference(form.slug, fields["full_name"], submitted_at),
        snapshot=snapshot,
        total=total,
        resolution_due=timezone.now() + timedelta(days=settings.CONTENT_RETENTION_DAYS),
        **fields,
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
