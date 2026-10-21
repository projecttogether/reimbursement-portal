import uuid
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from portal.models import Claim, Delivery, DeliveryAttempt, DeliveryEvent
from portal.services.drafts import digest

EVENT_STATES = {
    "activity.sent": "provider_accepted",
    "activity.delivered": "delivered",
    "activity.deferred": "provider_accepted",
    "activity.soft_bounced": "provider_accepted",
    "activity.hard_bounced": "failed",
    "activity.suppressed": "failed",
    "reconciliation.rejected": "failed",
}


def update_delivery(delivery):
    attempts = list(delivery.attempts.order_by("created_at"))
    if not attempts:
        return
    successful = [attempt for attempt in attempts if attempt.state == "delivered"]
    current = successful[-1] if successful else attempts[-1]
    delivery.state = current.state
    delivery.message_id = current.message_id or ""
    delivery.error_code = current.error_code
    claim = Claim.objects.select_for_update().get(pk=delivery.claim_id)
    if current.state == "delivered":
        delivery.delivered_at = delivery.delivered_at or timezone.now()
        claim.retention_due = delivery.delivered_at + timedelta(
            days=settings.SUCCESS_RETENTION_DAYS
        )
    elif current.state in {"failed", "send_unknown"}:
        claim.retention_due = None
    claim.save(update_fields=["retention_due"])
    delivery.save()


@transaction.atomic
def ingest_event(data):
    body = data.get("data", {})
    timestamp = parse_datetime(data.get("created_at", ""))
    provider_id = body.get("id")
    recipient = body.get("recipient")
    if data.get("type") not in EVENT_STATES:
        return
    if (
        not isinstance(provider_id, str)
        or not 1 <= len(provider_id) <= 100
        or not isinstance(recipient, str)
        or len(recipient) > 254
        or not timestamp
        or timezone.is_naive(timestamp)
    ):
        raise ValueError("invalid_event")
    tags = body.get("tags", [])
    attempt_tag = (
        next(
            (
                tag[8:]
                for tag in tags
                if isinstance(tag, str) and tag.startswith("attempt-") and len(tag) == 44
            ),
            "",
        )
        if isinstance(tags, list)
        else ""
    )
    message_id = body.get("message_id", "")
    if not isinstance(message_id, str) or len(message_id) > 100:
        raise ValueError("invalid_event")
    event, _ = DeliveryEvent.objects.get_or_create(
        provider_id=provider_id,
        defaults={
            "kind": data["type"],
            "provider_timestamp": timestamp,
            "message_id": message_id,
            "recipient_hash": digest(recipient.lower()),
            "attempt_tag": attempt_tag,
        },
    )
    apply_event(event)


def apply_event(event):
    if event.applied:
        return
    query = (
        DeliveryAttempt.objects.filter(message_id=event.message_id)
        if event.message_id
        else DeliveryAttempt.objects.none()
    )
    attempt = query.first()
    if not attempt and event.attempt_tag:
        try:
            attempt = DeliveryAttempt.objects.filter(pk=uuid.UUID(event.attempt_tag)).first()
        except (ValueError, TypeError):
            return
    if not attempt:
        return
    delivery = Delivery.objects.select_for_update().get(pk=attempt.delivery_id)
    attempt = DeliveryAttempt.objects.select_for_update().get(pk=attempt.pk)
    if digest(delivery.recipient.lower()) != event.recipient_hash or delivery.claim.state in {
        Claim.State.PURGING,
        Claim.State.PURGED,
    }:
        return
    if attempt.message_id and event.message_id and attempt.message_id != event.message_id:
        return
    if not attempt.message_id and event.message_id:
        attempt.message_id = event.message_id
    state = EVENT_STATES[event.kind]
    # Terminal success/failure cannot regress on a late sent/deferred callback.
    if state in {"failed", "delivered"} or attempt.state not in {"delivered", "failed"}:
        if not (attempt.state == "failed" and state == "delivered"):
            attempt.state = state
            attempt.error_code = (
                f"provider_{event.kind.rsplit('.', 1)[-1]}" if state == "failed" else ""
            )
    attempt.save()
    event.attempt, event.applied = attempt, True
    event.save(update_fields=["attempt", "applied"])
    update_delivery(delivery)


@transaction.atomic
def reconcile_unmatched():
    events = DeliveryEvent.objects.filter(applied=False).order_by(
        F("last_checked_at").asc(nulls_first=True), "received_at", "pk"
    )[:100]
    for event in list(events):
        event.last_checked_at = timezone.now()
        event.save(update_fields=["last_checked_at"])
        apply_event(event)
