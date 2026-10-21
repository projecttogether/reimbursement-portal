import logging
import random
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from portal.models import Claim, Delivery, DeliveryAttempt, Job, Upload
from portal.services import uploads
from portal.services.capacity import CapacityError, temporary_workspace
from portal.services.delivery import reconcile_unmatched, update_delivery
from portal.services.documents import ProcessingError, build_claim_pdf, validate_upload
from portal.services.mailersend import send_pdf
from portal.services.scanner import ScanError, scan_file
from portal.services.storage import claim_pdf_key, path_for, publish_file, remove_keys

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
            delivery = Delivery.objects.select_for_update().get(pk=job.delivery_id)
            delivery.attempts.filter(state="sending").update(
                state="send_unknown", error_code="worker_interrupted"
            )
            update_delivery(delivery)
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
            delivery, _ = Delivery.objects.get_or_create(
                claim=claim, defaults={"recipient": claim.snapshot["recipient"]}
            )
            Job.objects.get_or_create(
                key=f"send:{delivery.pk}", defaults={"kind": "send", "delivery": delivery}
            )
            finish_job(job)


def process_send(job):
    with transaction.atomic():
        if not live_job(job):
            return
        delivery = (
            Delivery.objects.select_for_update().select_related("claim").get(pk=job.delivery_id)
        )
        if (
            delivery.state in {"delivered", "provider_accepted", "paused", "send_unknown"}
            or delivery.claim.state != Claim.State.READY
        ):
            finish_job(job, "cancelled")
            return
        attempt = DeliveryAttempt.objects.create(delivery=delivery)
        delivery.state = "sending"
        delivery.save()
    result = send_pdf(attempt)
    with transaction.atomic():
        delivery = Delivery.objects.select_for_update().get(pk=delivery.pk)
        current = DeliveryAttempt.objects.select_for_update().get(pk=attempt.pk)
        if current.state not in {"delivered", "failed"}:
            current.state, current.error_code = result.state, result.error_code
        if result.message_id:
            current.message_id = result.message_id
        current.save()
        update_delivery(delivery)
        if result.state == "pending":
            if job.attempts <= len(BACKOFF):
                delay = max(result.retry_after, BACKOFF[job.attempts - 1]) + random.randint(0, 15)
                finish_job(job, "pending", result.error_code, delay)
            else:
                current.state, current.error_code = "failed", "retry_exhausted"
                current.save()
                update_delivery(delivery)
                finish_job(job, "failed", "retry_exhausted")
        else:
            finish_job(
                job,
                "failed" if result.state in {"failed", "send_unknown"} else "done",
                result.error_code,
            )
    reconcile_unmatched()


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
                    delivery = Delivery.objects.select_for_update().get(pk=job.delivery_id)
                    delivery.attempts.filter(state="sending").update(
                        state="send_unknown", error_code="unexpected_send_failure"
                    )
                    update_delivery(delivery)
                if job.claim_id:
                    Claim.objects.filter(pk=job.claim_id).update(
                        state=Claim.State.FAILED, error_code="processing_error"
                    )
                if job.upload_id:
                    uploads.mark_failed(job.upload_id, "processing_error")
                finish_job(job, "failed", "processing_error")
        logger.exception("job_failed job=%s kind=%s", job.pk, job.kind)
    return True
