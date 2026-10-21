import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from portal.models import (
    Claim,
    DeliveryEvent,
    Draft,
    DraftAdmission,
    Upload,
    WorkspaceReservation,
)
from portal.services import queue, uploads
from portal.services.capacity import recover_abandoned_workspaces
from portal.services.storage import outbox_key, remove_keys, sweep_orphans

logger = logging.getLogger(__name__)


class ClaimBusy(Exception):
    """A job or execution workspace still uses the claim; purge later."""


def purge_claim(claim_id):
    with transaction.atomic():
        claim = Claim.objects.select_for_update().get(pk=claim_id)
        if claim.state == Claim.State.PURGED:
            return
        if (
            queue.claim_has_running_job(claim)
            or WorkspaceReservation.objects.filter(Q(claim=claim) | Q(upload__claim=claim)).exists()
        ):
            raise ClaimBusy
        claim.state = Claim.State.PURGING
        claim.save(update_fields=["state"])
        queue.cancel_claim_jobs(claim)
    # Leave the manifest intact until every deletion succeeds, so a crash resumes safely.
    for upload in claim.uploads.all():
        uploads.remove_files(upload)
    remove_keys(claim.pdf_key)
    delivery = getattr(claim, "delivery", None)
    if delivery:
        for attempt in delivery.attempts.all():
            remove_keys(outbox_key(attempt.id))
    with transaction.atomic():
        claim = Claim.objects.select_for_update().get(pk=claim_id)
        if delivery:
            DeliveryEvent.objects.filter(attempt__delivery=delivery).delete()
            delivery.attempts.all().delete()
            delivery.recipient = delivery.message_id = delivery.error_code = ""
            delivery.save()
        claim.uploads.all().delete()
        claim.expenses.all().delete()
        for field in [
            "full_name",
            "email",
            "street",
            "postal_code",
            "city",
            "country",
            "account_holder",
            "iban",
            "bic",
            "purpose",
            "purpose_details",
            "trip_destination",
            "pdf_key",
            "pdf_hash",
            "error_code",
        ]:
            setattr(claim, field, "")
        claim.snapshot, claim.total = {}, None
        claim.trip_start = claim.trip_end = None
        claim.pdf_size = claim.pdf_page_count = 0
        claim.state, claim.purged_at = Claim.State.PURGED, timezone.now()
        claim.save()
        Draft.objects.filter(claim=claim).update(session_hash="", token_hash="", payload_hash="")


def remove_pdf(claim_id):
    """Delete a delivered claim's PDF and local email copies; the content stays."""
    with transaction.atomic():
        # The row lock keeps operator retries out until the PDF is gone.
        claim = Claim.objects.select_for_update().get(pk=claim_id)
        if not claim.pdf_key or claim.state in {Claim.State.PURGING, Claim.State.PURGED}:
            return
        if queue.claim_has_active_job(claim):
            raise ClaimBusy
        remove_keys(claim.pdf_key)
        delivery = getattr(claim, "delivery", None)
        if delivery:
            for attempt in delivery.attempts.all():
                remove_keys(outbox_key(attempt.id))
        claim.pdf_key, claim.pdf_removed_at = "", timezone.now()
        claim.save(update_fields=["pdf_key", "pdf_removed_at"])


def cleanup(*, execute=False):
    now = timezone.now()
    pdfs = list(
        Claim.objects.filter(pdf_due__lte=now)
        .exclude(pdf_key="")
        .exclude(state__in=[Claim.State.PURGING, Claim.State.PURGED])
        .values_list("pk", flat=True)
    )
    due = list(
        Claim.objects.filter(retention_due__lte=now)
        .exclude(state=Claim.State.PURGED)
        .values_list("pk", flat=True)
    )
    due += list(
        Claim.objects.filter(state=Claim.State.PURGING)
        .exclude(pk__in=due)
        .values_list("pk", flat=True)
    )
    drafts = list(
        Draft.objects.filter(claim__isnull=True, expires_at__lt=now).values_list("pk", flat=True)
    )
    unresolved = (
        Claim.objects.filter(
            resolution_due__lte=now + timedelta(days=3), retention_due__isnull=True
        )
        .exclude(state__in=[Claim.State.PURGED, Claim.State.PURGING])
        .count()
    )
    result = {
        "pdfs_due": len(pdfs),
        "claims_due": len(due),
        "drafts_due": len(drafts),
        "unresolved_near_expiry": unresolved,
        "failures": 0,
    }
    if not execute:
        return result
    DraftAdmission.objects.filter(window_start__lt=now - timedelta(days=2)).delete()
    for claim_id in pdfs if settings.CONTENT_PURGE_ENABLED else []:
        try:
            remove_pdf(claim_id)
        except (OSError, ClaimBusy):
            result["failures"] += 1
            logger.error("pdf_removal_failed reference=%s", claim_id)
    for claim_id in due:
        if (
            not settings.CONTENT_PURGE_ENABLED
            and not Claim.objects.filter(pk=claim_id, state=Claim.State.PURGING).exists()
        ):
            continue
        try:
            purge_claim(claim_id)
        except (OSError, ClaimBusy):
            result["failures"] += 1
            logger.error("purge_failed reference=%s", claim_id)
    for draft_id in drafts:
        try:
            uploads.expire_draft(draft_id)
        except OSError:
            result["failures"] += 1
    Draft.objects.filter(claim__state=Claim.State.PURGED, expires_at__lt=now).delete()
    result["failures"] += uploads.expire_unattached(now)
    DeliveryEvent.objects.filter(
        attempt__isnull=True, received_at__lt=now - timedelta(days=30)
    ).delete()
    # Minimal reference/status records have a bounded operational lifetime.
    Claim.objects.filter(state=Claim.State.PURGED, purged_at__lt=now - timedelta(days=30)).delete()
    orphan_cleanup()
    return result


class StoredOwners:
    """Database ownership of stored artifacts, for the storage orphan sweep."""

    def upload_keys(self, upload_id):
        upload = Upload.objects.filter(pk=upload_id).first()
        return {upload.source_key, upload.normalized_key} if upload else None

    def workspace_reserved(self, key):
        return WorkspaceReservation.objects.filter(directory=key).exists()

    def pdf_referenced(self, key):
        return Claim.objects.filter(pdf_key=key).exists()


def orphan_cleanup():
    recover_abandoned_workspaces()
    sweep_orphans(timezone.now() - timedelta(hours=24), StoredOwners())
