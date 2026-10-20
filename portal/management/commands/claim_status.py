import json

from django.core.management.base import BaseCommand
from django.utils import timezone

from portal.models import Claim, Job


class Command(BaseCommand):
    help = "Inspect operational state without printing participant details."

    def add_arguments(self, parser):
        parser.add_argument("reference", nargs="?")

    def handle(self, *args, **options):
        claims = Claim.objects.order_by("submitted_at")
        if options["reference"]:
            claims = claims.filter(pk=options["reference"])
        for claim in claims[:100]:
            delivery = getattr(claim, "delivery", None)
            self.stdout.write(
                json.dumps(
                    {
                        "reference": claim.reference,
                        "state": claim.state,
                        "submitted_at": claim.submitted_at.isoformat(),
                        "delivery_state": delivery.state if delivery else None,
                        "error_code": claim.error_code or (delivery.error_code if delivery else ""),
                        "resolution_overdue": claim.resolution_due <= timezone.now()
                        and not claim.retention_due
                        and claim.state != Claim.State.PURGED,
                    }
                )
            )
        self.stdout.write(
            json.dumps(
                {
                    "pending_jobs": Job.objects.filter(state="pending").count(),
                    "failed_jobs": Job.objects.filter(state="failed").count(),
                }
            )
        )
