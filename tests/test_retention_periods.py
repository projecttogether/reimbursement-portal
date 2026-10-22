from datetime import timedelta

import pytest
from django.utils import timezone

from portal.models import Claim, DeliveryAttempt
from portal.services import delivery as deliveries
from portal.services.retention import cleanup
from portal.services.storage import path_for

pytestmark = pytest.mark.django_db


def delivered(delivery):
    DeliveryAttempt.objects.create(delivery=delivery, state="delivered", message_id="message-1")
    deliveries.refresh(delivery)
    claim = Claim.objects.get(pk=delivery.claim_id)
    return claim


def test_delivery_schedules_pdf_and_content_deletion(delivery, settings):
    claim = delivered(delivery)
    delivery.refresh_from_db()
    assert claim.pdf_due == delivery.delivered_at + timedelta(days=settings.PDF_RETENTION_DAYS)
    assert claim.retention_due == claim.submitted_at + timedelta(
        days=settings.CONTENT_RETENTION_DAYS
    )


def test_pdf_is_deleted_first_and_content_after_thirty_days(delivery):
    claim = delivered(delivery)
    pdf = path_for(claim.pdf_key)
    Claim.objects.filter(pk=claim.pk).update(pdf_due=timezone.now() - timedelta(seconds=1))
    assert cleanup()["pdfs_due"] == 1
    result = cleanup(execute=True)
    assert result["failures"] == 0
    claim.refresh_from_db()
    assert not pdf.exists() and claim.pdf_key == "" and claim.pdf_removed_at
    assert claim.state == Claim.State.READY and claim.iban and claim.uploads.count() == 2
    Claim.objects.filter(pk=claim.pk).update(retention_due=timezone.now() - timedelta(seconds=1))
    cleanup(execute=True)
    claim.refresh_from_db()
    assert claim.state == Claim.State.PURGED and not claim.iban


def test_pdf_deletion_waits_for_the_handoff_policy(delivery, settings):
    settings.CONTENT_PURGE_ENABLED = False
    claim = delivered(delivery)
    Claim.objects.filter(pk=claim.pk).update(pdf_due=timezone.now() - timedelta(seconds=1))
    cleanup(execute=True)
    claim.refresh_from_db()
    assert claim.pdf_key and path_for(claim.pdf_key).exists()


def test_failed_delivery_keeps_the_pdf_for_resolution(delivery):
    DeliveryAttempt.objects.create(delivery=delivery, state="failed")
    deliveries.refresh(delivery)
    claim = Claim.objects.get(pk=delivery.claim_id)
    assert claim.pdf_due is None and claim.retention_due is None
    cleanup(execute=True)
    claim.refresh_from_db()
    assert claim.pdf_key
