import json

from django.core.management.base import BaseCommand, CommandError
from django.db.models import F
from django.utils import timezone

from portal.models import DeliveryAttempt
from portal.services.delivery import ingest_event, reconcile_unmatched
from portal.services.mailersend import message_snapshot, retrieve_message


class Command(BaseCommand):
    help = "Reconcile known message IDs without resending. Unknown sends require manual provider lookup."

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=100)

    def handle(self, *args, **options):
        if not 1 <= options["batch_size"] <= 1000:
            raise CommandError("batch-size must be between 1 and 1000")
        reconcile_unmatched()
        attempts = (
            DeliveryAttempt.objects.exclude(message_id__isnull=True)
            .filter(state__in=["provider_accepted", "paused", "send_unknown"])
            .order_by(F("last_checked_at").asc(nulls_first=True), "created_at", "pk")[
                : options["batch_size"]
            ]
        )
        counts = {"checked": 0, "deferred": 0, "failed": 0}
        for attempt in list(attempts):
            DeliveryAttempt.objects.filter(pk=attempt.pk).update(last_checked_at=timezone.now())
            counts["checked"] += 1
            message = retrieve_message(attempt.message_id)
            if message is None:
                counts["failed"] += 1
                continue
            snapshot = message_snapshot(message)
            if not snapshot or not snapshot.get("recipient"):
                counts["deferred"] += 1
                continue

            ingest_event(
                {
                    "type": "reconciliation.rejected"
                    if snapshot["status"] == "rejected"
                    else f"activity.{snapshot['status']}",
                    "created_at": timezone.now().isoformat(),
                    "data": {
                        "id": f"reconcile-{attempt.id}-{snapshot['status']}",
                        "message_id": attempt.message_id,
                        "recipient": snapshot["recipient"],
                    },
                }
            )
        self.stdout.write(json.dumps(counts))
        if counts["failed"]:
            raise CommandError(f"Provider lookup failed for {counts['failed']} attempts.")
