"""Admission and disk accounting shared by web processes and document workers."""

import shutil
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from portal.models import CapacityLock, Job, Upload, WorkspaceReservation
from portal.services.draft_limits import locked_budget


class CapacityError(Exception):
    pass


def total(query, field):
    return query.aggregate(value=Sum(field))["value"] or 0


def check_storage(additional):
    """Caller holds an atomic transaction; the lock lasts through publication."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("Capacity admission requires a transaction")
    CapacityLock.objects.select_for_update().get_or_create(pk=1)
    reserved = total(Upload.objects.all(), "reserved_bytes") + total(
        WorkspaceReservation.objects.all(), "size"
    )
    free = shutil.disk_usage(settings.PRIVATE_STORAGE_ROOT).free
    if free < (
        settings.MIN_FREE_STORAGE_BYTES
        + settings.UPLOAD_TEMP_HEADROOM_BYTES
        + reserved
        + additional
    ):
        raise CapacityError("storage_capacity")


def admit_upload(request, draft, size):
    # The caller holds Draft before Network before Capacity. No worker takes a
    # Draft lock while holding a Job lock, including reservation release paths.
    if (
        draft.upload_count >= settings.MAX_UPLOADS_PER_DRAFT
        or draft.upload_bytes + size > settings.MAX_UPLOAD_BYTES_PER_DRAFT
    ):
        raise CapacityError("draft_work_limit")
    budget = locked_budget(request)
    now = timezone.now()
    if budget.upload_window_start <= now - timedelta(minutes=1):
        budget.upload_window_start, budget.upload_window_count = now, 0
    if (
        budget.upload_count >= settings.MAX_UPLOADS_PER_NETWORK
        or budget.upload_bytes + size > settings.MAX_UPLOAD_BYTES_PER_NETWORK
        or budget.upload_window_count >= settings.MAX_UPLOADS_PER_NETWORK_PER_MINUTE
    ):
        raise CapacityError("network_work_limit")
    reservation = settings.MAX_NORMALIZED_FILE_BYTES
    check_storage(size + reservation)
    derived = (
        total(draft.uploads.all(), "normalized_size")
        + total(draft.uploads.all(), "reserved_bytes")
        + total(WorkspaceReservation.objects.filter(upload__draft=draft), "size")
    )
    if derived + reservation > settings.MAX_DRAFT_DERIVED_BYTES:
        raise CapacityError("draft_storage_limit")
    if (
        Job.objects.filter(kind="validate", state__in=["pending", "running"]).count()
        >= settings.MAX_PENDING_VALIDATIONS
    ):
        raise CapacityError("validation_backlog")
    draft.upload_count += 1
    draft.upload_bytes += size
    draft.save(update_fields=["upload_count", "upload_bytes"])
    budget.upload_count += 1
    budget.upload_bytes += size
    budget.upload_window_count += 1
    budget.save()
    return reservation
