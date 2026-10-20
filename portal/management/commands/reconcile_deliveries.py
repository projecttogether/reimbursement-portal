from django.core.management.base import BaseCommand

from portal.models import DeliveryAttempt
from portal.services.delivery import ingest_event, reconcile_unmatched
from portal.services.mailersend import message_snapshot, retrieve_message


class Command(BaseCommand):
    help = "Reconcile known message IDs without resending. Unknown sends require manual provider lookup."

    def handle(self, *args, **options):
        reconcile_unmatched()
        attempts = (
            DeliveryAttempt.objects.exclude(message_id__isnull=True)
            .filter(state__in=["provider_accepted", "paused", "send_unknown"])
            .order_by("created_at")[:50]
        )
        for attempt in attempts:
            snapshot = message_snapshot(retrieve_message(attempt.message_id))
            if not snapshot or not snapshot.get("recipient"):
                continue
            from django.utils import timezone

            ingest_event(
                {
                    "type": f"activity.{snapshot['status']}",
                    "created_at": timezone.now().isoformat(),
                    "data": {
                        "id": f"reconcile-{attempt.id}-{snapshot['status']}",
                        "message_id": attempt.message_id,
                        "recipient": snapshot["recipient"],
                    },
                }
            )
        self.stdout.write("Reconciliation complete. No messages were resent.")
