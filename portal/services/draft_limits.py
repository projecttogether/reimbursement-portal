from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac

from portal.models import DraftAdmission


@transaction.atomic
def reserve_draft(network):
    budget = locked_budget(network)
    if budget.count >= settings.MAX_DRAFTS_PER_NETWORK:
        return False
    budget.count += 1
    budget.save()
    return True


def locked_budget(network):
    key = salted_hmac("draft-admission", network).hexdigest()
    budget, _ = DraftAdmission.objects.select_for_update().get_or_create(key=key)
    now = timezone.now()
    if budget.window_start <= now - timedelta(hours=24):
        budget.window_start, budget.count = now, 0
        budget.upload_count = budget.upload_bytes = 0
        budget.upload_window_start, budget.upload_window_count = now, 0
    return budget
