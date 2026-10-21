import getpass
import uuid

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from portal.models import Claim, Job, Operation
from portal.services import delivery


class Command(BaseCommand):
    help = "Retry a failed render/send. Ambiguous or accepted sends require explicit duplicate-risk acknowledgment."

    def add_arguments(self, parser):
        parser.add_argument("reference")
        parser.add_argument("--reason", required=True)
        parser.add_argument("--actor", default=getpass.getuser())
        parser.add_argument("--acknowledge-duplicate-risk", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        claim = Claim.objects.select_for_update().get(pk=options["reference"])
        if (
            Job.objects.filter(claim=claim, state__in=["pending", "running"]).exists()
            or Job.objects.filter(delivery__claim=claim, state__in=["pending", "running"]).exists()
        ):
            raise CommandError("This claim already has an active job.")
        if claim.state == Claim.State.FAILED and not claim.pdf_key:
            claim.state, claim.error_code = Claim.State.RECEIVED, ""
            claim.save()
            Job.objects.create(key=f"render:{claim.pk}:{uuid.uuid4()}", kind="render", claim=claim)
        elif claim.state == Claim.State.READY:
            try:
                delivery.retry(
                    claim, acknowledge_duplicate_risk=options["acknowledge_duplicate_risk"]
                )
            except delivery.DuplicateRisk as exc:
                raise CommandError(
                    "Check provider activity first. Use --acknowledge-duplicate-risk for an intentional resend."
                ) from exc
        else:
            raise CommandError("This claim cannot be retried in its current state.")
        Operation.objects.create(
            claim=claim, action="retry", actor=options["actor"], reason=options["reason"]
        )
        self.stdout.write(f"Queued retry for {claim.reference}.")
