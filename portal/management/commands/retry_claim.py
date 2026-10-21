import getpass

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from portal.job_kinds import RENDER
from portal.models import Claim, Operation
from portal.services import delivery, queue


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
        if queue.claim_has_active_job(claim):
            raise CommandError("This claim already has an active job.")
        if claim.state == Claim.State.READY:
            try:
                delivery.retry(
                    claim, acknowledge_duplicate_risk=options["acknowledge_duplicate_risk"]
                )
            except delivery.DuplicateRisk as exc:
                raise CommandError(
                    "Check provider activity first. Use --acknowledge-duplicate-risk for an intentional resend."
                ) from exc
        elif not RENDER.requeue(claim):
            raise CommandError("This claim cannot be retried in its current state.")
        Operation.objects.create(
            claim=claim, action="retry", actor=options["actor"], reason=options["reason"]
        )
        self.stdout.write(f"Queued retry for {claim.reference}.")
