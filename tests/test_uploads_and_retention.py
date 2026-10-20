from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from portal.jobs import run_one
from portal.models import Job, Upload
from portal.services.documents import ProcessingError
from portal.services.retention import cleanup, purge_claim
from portal.services.storage import path_for
from tests.conftest import image_bytes

pytestmark = pytest.mark.django_db


def test_real_upload_is_validated_asynchronously(prepared, uploaded_file):
    data = {
        "draft_id": str(prepared["draft"].id),
        "token": prepared["data"]["token"],
        "kind": "receipt",
        "expense_id": prepared["data"]["expenses"][0]["id"],
        "file": uploaded_file,
    }
    response = prepared["client"].post("/forms/test/uploads/", data)
    assert response.status_code == 202, response.content
    result = response.json()
    upload = Upload.objects.get(pk=result["id"])
    assert upload.state == "pending"
    assert run_one()
    upload.refresh_from_db()
    assert upload.state == "ready", upload.error_code
    assert upload.normalized_key.endswith(".jpg")
    assert prepared["client"].get(result["status_url"]).json()["pages"] == 1


def test_scanner_failure_keeps_file_pending(prepared, uploaded_file):
    response = prepared["client"].post(
        "/forms/test/uploads/",
        {
            "draft_id": str(prepared["draft"].id),
            "token": prepared["data"]["token"],
            "kind": "receipt",
            "expense_id": prepared["data"]["expenses"][0]["id"],
            "file": uploaded_file,
        },
    )
    with patch("portal.jobs.scan_file", side_effect=ProcessingError("scanner_unavailable")):
        run_one()
    upload = Upload.objects.get(pk=response.json()["id"])
    assert upload.state == "pending"
    assert upload.error_code == "scanner_unavailable"
    assert Job.objects.get(upload=upload).state == "pending"


def test_original_single_file_limit_is_enforced(prepared):
    response = prepared["client"].post(
        "/forms/test/uploads/",
        {
            "draft_id": str(prepared["draft"].id),
            "token": prepared["data"]["token"],
            "kind": "receipt",
            "expense_id": prepared["data"]["expenses"][0]["id"],
            "file": SimpleUploadedFile("large.png", b"x" * 10_000_001, content_type="image/png"),
        },
    )
    assert response.status_code == 413
    assert Upload.objects.count() == 2


def test_spoofed_image_never_becomes_ready(prepared):
    response = prepared["client"].post(
        "/forms/test/uploads/",
        {
            "draft_id": str(prepared["draft"].id),
            "token": prepared["data"]["token"],
            "kind": "receipt",
            "expense_id": prepared["data"]["expenses"][0]["id"],
            "file": SimpleUploadedFile("receipt.png", b"not an image", content_type="image/png"),
        },
    )
    assert response.status_code == 202
    run_one()
    assert Upload.objects.get(pk=response.json()["id"]).state == "failed"


def test_purge_removes_all_sensitive_fields_and_files(accepted):
    while run_one():
        pass
    accepted.refresh_from_db()
    files = [path_for(accepted.pdf_key)] + [
        path_for(upload.source_key) for upload in accepted.uploads.all()
    ]
    purge_claim(accepted.pk)
    accepted.refresh_from_db()
    assert accepted.state == "purged"
    assert all(not path.exists() for path in files)
    assert not accepted.iban and not accepted.email and not accepted.purpose
    assert accepted.snapshot == {} and accepted.totals == {}
    assert not accepted.expenses.exists() and not accepted.uploads.exists()
    assert accepted.delivery.recipient == ""
    assert not accepted.delivery.attempts.exists()


def test_interrupted_purge_resumes_from_manifest(accepted):
    with patch("portal.services.retention.remove_keys", side_effect=OSError("synthetic failure")):
        with pytest.raises(OSError):
            purge_claim(accepted.pk)
    accepted.refresh_from_db()
    assert accepted.state == "purging"
    assert accepted.uploads.count() == 2
    cleanup(execute=True)
    accepted.refresh_from_db()
    assert accepted.state == "purged"


def test_unresolved_claim_is_alerted_not_silently_deleted(accepted):
    accepted.resolution_due = timezone.now() - timedelta(days=1)
    accepted.save()
    result = cleanup(execute=True)
    accepted.refresh_from_db()
    assert result["unresolved_near_expiry"] == 1
    assert accepted.state != "purged"
    assert accepted.uploads.exists()


def test_unused_upload_expires_after_claim_acceptance(accepted):
    import uuid

    key = f"uploads/{uuid.uuid4()}/source.png"
    path = path_for(key)
    path.parent.mkdir(parents=True)
    path.write_bytes(image_bytes())
    extra = Upload.objects.create(
        draft=accepted.accepted_draft,
        kind="receipt",
        original_name="unused.png",
        source_key=key,
        size=path.stat().st_size,
        checksum="synthetic",
        created_at=timezone.now() - timedelta(hours=25),
    )
    cleanup(execute=True)
    assert not Upload.objects.filter(pk=extra.pk).exists()
    assert not path.exists()
    assert accepted.uploads.count() == 2


def test_liveness_is_independent_of_database(client):
    with patch(
        "portal.views.connection.cursor", side_effect=RuntimeError("synthetic database outage")
    ):
        assert client.get("/-/healthz").status_code == 200
        assert client.get("/-/readyz").status_code == 503


def test_content_deletion_waits_for_configured_handoff_policy(accepted, settings):
    settings.CONTENT_PURGE_ENABLED = False
    accepted.retention_due = timezone.now() - timedelta(seconds=1)
    accepted.save()
    cleanup(execute=True)
    accepted.refresh_from_db()
    assert accepted.state != "purged" and accepted.iban
    settings.CONTENT_PURGE_ENABLED = True
    cleanup(execute=True)
    accepted.refresh_from_db()
    assert accepted.state == "purged"


def test_purged_claim_does_not_match_an_anonymous_session(accepted):
    from django.test import Client

    purge_claim(accepted.pk)
    assert Client().get(f"/received/{accepted.pk}/").status_code == 404


def test_public_health_requires_deployment_token(client, settings):
    settings.DEBUG = False
    settings.HEALTHCHECK_TOKEN = "synthetic-health-token"
    assert client.get("/-/healthz").status_code == 404
    assert (
        client.get("/-/healthz", headers={"X-Health-Token": "synthetic-health-token"}).status_code
        == 200
    )
