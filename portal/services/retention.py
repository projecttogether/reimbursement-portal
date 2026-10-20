import logging
import shutil
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from portal.models import Claim, DeliveryEvent, Draft, DraftAdmission, Job, Upload
from portal.services.documents import ProcessingError
from portal.services.storage import remove_keys

logger = logging.getLogger(__name__)


def purge_claim(claim_id):
    with transaction.atomic():
        claim = Claim.objects.select_for_update().get(pk=claim_id)
        if claim.state == Claim.State.PURGED:
            return
        if (
            Job.objects.filter(state="running").filter(claim=claim).exists()
            or Job.objects.filter(state="running", delivery__claim=claim).exists()
        ):
            raise ProcessingError("claim_busy")
        claim.state = Claim.State.PURGING
        claim.save(update_fields=["state"])
        Job.objects.filter(claim=claim).update(state="cancelled")
        Job.objects.filter(delivery__claim=claim).update(state="cancelled")
    # Leave the manifest intact until every deletion succeeds, so a crash resumes safely.
    for upload in claim.uploads.all():
        remove_keys(upload.source_key, upload.normalized_key)
    remove_keys(claim.pdf_key)
    delivery = getattr(claim, "delivery", None)
    if delivery:
        for attempt in delivery.attempts.all():
            remove_keys(f"outbox/{attempt.id}.eml")
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
            "postal_address",
            "email",
            "iban",
            "purpose",
            "trip_location",
            "pdf_key",
            "pdf_hash",
            "error_code",
        ]:
            setattr(claim, field, "")
        claim.snapshot, claim.totals = {}, {}
        claim.trip_start = claim.trip_end = None
        claim.pdf_size = claim.pdf_page_count = 0
        claim.state, claim.purged_at = Claim.State.PURGED, timezone.now()
        claim.save()
        Draft.objects.filter(claim=claim).update(session_hash="", token_hash="", payload_hash="")


def cleanup(*, execute=False):
    now = timezone.now()
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
        "claims_due": len(due),
        "drafts_due": len(drafts),
        "unresolved_near_expiry": unresolved,
        "failures": 0,
    }
    if not execute:
        return result
    DraftAdmission.objects.filter(window_start__lt=now - timedelta(days=2)).delete()
    for claim_id in due:
        if (
            not settings.CONTENT_PURGE_ENABLED
            and not Claim.objects.filter(pk=claim_id, state=Claim.State.PURGING).exists()
        ):
            continue
        try:
            purge_claim(claim_id)
        except (OSError, ProcessingError):
            result["failures"] += 1
            logger.error("purge_failed reference=%s", claim_id)
    for draft_id in drafts:
        with transaction.atomic():
            draft = (
                Draft.objects.select_for_update().filter(pk=draft_id, claim__isnull=True).first()
            )
            if not draft or Job.objects.filter(upload__draft=draft, state="running").exists():
                continue
            try:
                for upload in draft.uploads.all():
                    remove_keys(upload.source_key, upload.normalized_key)
                draft.delete()
            except OSError:
                result["failures"] += 1
    Draft.objects.filter(claim__state=Claim.State.PURGED, expires_at__lt=now).delete()
    # Accepted drafts can contain uploads removed from the final manifest. Those
    # files retain the draft lifetime rather than the claim's longer retention.
    unused = Upload.objects.filter(
        claim__isnull=True, draft__claim__isnull=False, created_at__lt=now - timedelta(hours=24)
    )
    for upload in unused:
        if not Job.objects.filter(upload=upload, state="running").exists():
            try:
                remove_keys(upload.source_key, upload.normalized_key)
                upload.delete()
            except OSError:
                result["failures"] += 1
    DeliveryEvent.objects.filter(
        attempt__isnull=True, received_at__lt=now - timedelta(days=30)
    ).delete()
    # Minimal reference/status records have a bounded operational lifetime.
    Claim.objects.filter(state=Claim.State.PURGED, purged_at__lt=now - timedelta(days=30)).delete()
    orphan_cleanup()
    return result


def orphan_cleanup():
    cutoff = (timezone.now() - timedelta(hours=24)).timestamp()
    root = settings.PRIVATE_STORAGE_ROOT
    for directory in (root / "uploads").glob("*"):
        if directory.is_dir() and directory.stat().st_mtime < cutoff:
            try:
                exists = Upload.objects.filter(pk=directory.name).exists()
            except (ValueError, TypeError):
                continue
            if not exists:
                shutil.rmtree(directory)
    for directory in (root / "work").glob("job-*"):
        if directory.is_dir() and directory.stat().st_mtime < cutoff:
            shutil.rmtree(directory)
    for file in (root / "pdf").glob("*/*.pdf"):
        if (
            file.stat().st_mtime < cutoff
            and not Claim.objects.filter(pdf_key=str(file.relative_to(root))).exists()
        ):
            file.unlink()
    for file in (root / "incoming").glob("*"):
        if file.is_file() and file.stat().st_mtime < cutoff:
            file.unlink()
