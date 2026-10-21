import logging
import random
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from portal.models import Claim, Job, Upload
from portal.services import delivery, uploads
from portal.services.capacity import CapacityError, temporary_workspace
from portal.services.documents import ProcessingError, build_claim_pdf, validate_upload
from portal.services.scanner import ScanError, scan_file
from portal.services.storage import claim_pdf_key, path_for, publish_file, remove_keys
from portal.services.transport import FAILED, PENDING, UNKNOWN

logger = logging.getLogger(__name__)
BACKOFF = [60, 300, 900, 3600]


@transaction.atomic
def acquire_job():
    # Later public intake cannot overtake already accepted work. run_after also
    # keeps backoff retries behind work that was ready before their retry time.
    query = Job.objects.filter(state="pending", run_after__lte=timezone.now()).order_by(
        "run_after", "id"
    )
    if connection.features.has_select_for_update_skip_locked:
        query = query.select_for_update(skip_locked=True)
    else:
        query = query.select_for_update()
    job = query.first()
    if job:
        job.state, job.lease_token = "running", uuid.uuid4()
        job.lease_until = timezone.now() + timedelta(seconds=settings.JOB_LEASE_SECONDS)
        job.attempts += 1
        job.save()
    return job


@transaction.atomic
def reclaim_stale_jobs():
    for job in Job.objects.select_for_update().filter(
        state="running", lease_until__lt=timezone.now()
    ):
        if job.kind == "send":
            delivery.mark_interrupted(job.delivery_id, "worker_interrupted")
            job.state = "failed"
            job.error_code = "send_unknown"
        else:
            job.state = "pending" if job.attempts < 5 else "failed"
            job.error_code = "worker_interrupted"
            if job.state == "failed":
                if job.claim_id:
                    Claim.objects.filter(pk=job.claim_id).update(
                        state=Claim.State.FAILED, error_code="worker_interrupted"
                    )
                if job.upload_id:
                    uploads.mark_failed(job.upload_id, "worker_interrupted")
        job.lease_token, job.lease_until = None, None
        job.save()


def live_job(job):
    return (
        Job.objects.select_for_update()
        .filter(
            pk=job.pk, state="running", lease_token=job.lease_token, lease_until__gt=timezone.now()
        )
        .exists()
    )


def finish_job(job, state="done", error_code="", delay=0):
    Job.objects.filter(pk=job.pk, state="running", lease_token=job.lease_token).update(
        state=state,
        error_code=error_code,
        run_after=timezone.now() + timedelta(seconds=delay),
        lease_token=None,
        lease_until=None,
    )


def process_validation(job):
    upload = Upload.objects.get(pk=job.upload_id)
    with transaction.atomic():
        if not live_job(job):
            return
    scan_file(path_for(upload.source_key))
    with validate_upload(upload) as result:
        with transaction.atomic():
            if not live_job(job) or not uploads.mark_ready(upload.pk, result):
                uploads.discard_output(upload, result)
                return
            finish_job(job)


def process_render(job):
    with transaction.atomic():
        if not live_job(job):
            return
        claim = Claim.objects.select_for_update().get(pk=job.claim_id)
        if claim.state in {Claim.State.PURGING, Claim.State.PURGED}:
            finish_job(job, "cancelled")
            return
        claim.state = Claim.State.RENDERING
        claim.save(update_fields=["state"])
    with temporary_workspace(claim=claim) as workspace:
        source, result = build_claim_pdf(claim, workspace)
        with transaction.atomic():
            if not live_job(job):
                return
            claim = Claim.objects.select_for_update().get(pk=claim.pk)
            if claim.state in {Claim.State.PURGING, Claim.State.PURGED}:
                finish_job(job, "cancelled")
                return
            key = claim_pdf_key(claim.id, job.lease_token)
            size, checksum = publish_file(source, key, settings.MAX_FINAL_PDF_BYTES)
            if checksum != result["checksum"] or size != result["size"]:
                remove_keys(key)
                raise ProcessingError("invalid_processing_output")
            claim.pdf_key, claim.pdf_hash, claim.pdf_size, claim.pdf_page_count = (
                key,
                result["checksum"],
                result["size"],
                result["pages"],
            )
            claim.state, claim.error_code = Claim.State.READY, ""
            claim.save()
            delivery.schedule(claim)
            finish_job(job)


def process_send(job):
    with transaction.atomic():
        if not live_job(job):
            return
        attempt = delivery.start_attempt(job.delivery_id)
        if not attempt:
            finish_job(job, "cancelled")
            return
    result = delivery.send(attempt)
    with transaction.atomic():
        delivery.record_result(attempt.pk, result)
        if result.state == PENDING:
            if job.attempts <= len(BACKOFF):
                delay = max(result.retry_after, BACKOFF[job.attempts - 1]) + random.randint(0, 15)
                finish_job(job, "pending", result.error_code, delay)
            else:
                delivery.abandon(attempt.pk, "retry_exhausted")
                finish_job(job, "failed", "retry_exhausted")
        else:
            finish_job(
                job,
                "failed" if result.state in {FAILED, UNKNOWN} else "done",
                result.error_code,
            )
    delivery.reconcile_unmatched()


def run_one():
    job = acquire_job()
    if not job:
        return False
    try:
        {"validate": process_validation, "render": process_render, "send": process_send}[job.kind](
            job
        )
    except CapacityError:
        with transaction.atomic():
            if live_job(job):
                finish_job(job, "pending", "storage_capacity", 60)
        logger.warning("document_capacity_wait job=%s", job.pk)
    except (ProcessingError, ScanError) as exc:
        code = str(exc)
        with transaction.atomic():
            if live_job(job):
                retry = getattr(exc, "retryable", False) and job.attempts <= len(BACKOFF)
                finish_job(
                    job,
                    "pending" if retry else "failed",
                    code,
                    BACKOFF[min(job.attempts - 1, len(BACKOFF) - 1)],
                )
                if job.upload_id:
                    if retry:
                        uploads.mark_retrying(job.upload_id, code)
                    else:
                        uploads.mark_failed(job.upload_id, code)
                if job.claim_id:
                    Claim.objects.filter(pk=job.claim_id).update(
                        state=Claim.State.FAILED, error_code=code
                    )
        logger.warning("document_job_failed job=%s code=%s", job.pk, code)
    except Exception:
        # Preserve ambiguity for any unexpected exception around a provider call.
        with transaction.atomic():
            if live_job(job):
                if job.delivery_id:
                    delivery.mark_interrupted(job.delivery_id, "unexpected_send_failure")
                if job.claim_id:
                    Claim.objects.filter(pk=job.claim_id).update(
                        state=Claim.State.FAILED, error_code="processing_error"
                    )
                if job.upload_id:
                    uploads.mark_failed(job.upload_id, "processing_error")
                finish_job(job, "failed", "processing_error")
        logger.exception("job_failed job=%s kind=%s", job.pk, job.kind)
    return True
