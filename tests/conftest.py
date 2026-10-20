import io
import json
import uuid

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image, ImageDraw

from portal.models import Claim, Draft, Event, Upload
from portal.services.storage import path_for


@pytest.fixture(autouse=True)
def local_settings(settings, tmp_path):
    settings.DEBUG = True
    settings.PRIVATE_STORAGE_ROOT = tmp_path / "private"
    settings.PRIVATE_STORAGE_ROOT.mkdir()
    settings.FILE_UPLOAD_TEMP_DIR = str(settings.PRIVATE_STORAGE_ROOT / "incoming")
    (settings.PRIVATE_STORAGE_ROOT / "incoming").mkdir()
    settings.STATIC_ROOT = tmp_path / "staticfiles"
    settings.STATIC_ROOT.mkdir()
    settings.UPLOAD_SCANNER = "disabled"
    settings.MAIL_DELIVERY_MODE = "local"
    settings.CONTENT_PURGE_ENABLED = True
    settings.MIN_FREE_STORAGE_BYTES = 0
    settings.APPROVED_DELIVERY_RECIPIENTS = ["finance@example.invalid"]
    settings.TEST_RECIPIENT_ALLOWLIST = []


@pytest.fixture
def event(db):
    return Event.objects.create(
        slug="test",
        name="Test Workshop",
        cost_center="TEST-KST",
        cost_object="TEST-KTR",
        recipient="finance@example.invalid",
        support_email="finance@example.invalid",
        privacy_notice="Synthetic privacy notice for tests.",
        privacy_version="test-v1",
    )


def image_bytes(*, signature=False, color="white", size=(900, 1200)):
    image = Image.new("RGB", (800, 240) if signature else size, color)
    drawing = ImageDraw.Draw(image)
    if signature:
        drawing.line(
            [(80, 160), (150, 60), (210, 170), (300, 80), (440, 150), (600, 100)],
            fill="#00115a",
            width=5,
        )
    else:
        drawing.text((50, 60), "SYNTHETIC RECEIPT - TEST ONLY", fill="black")
        drawing.text((50, 110), "Total EUR 12.50", fill="black")
    output = io.BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def camera_jpeg_bytes(*, primary_color="forestgreen"):
    """A JPEG with an MPF header and a separate auxiliary picture."""
    primary = Image.new("RGB", (320, 180), primary_color)
    auxiliary = Image.new("RGB", (80, 60), "red")
    exif = Image.Exif()
    exif[274] = 6  # Camera orientation, 90 degrees clockwise.
    exif[270] = "Synthetic private camera metadata"
    output = io.BytesIO()
    primary.save(output, "MPO", save_all=True, append_images=[auxiliary], exif=exif)
    return output.getvalue()


@pytest.fixture
def prepared(client, event):
    response = client.get(f"/forms/{event.slug}/")
    assert response.status_code == 200
    draft = Draft.objects.get()
    token = client.session[f"draft:{event.slug}"]["token"]
    row_id = uuid.uuid4()
    receipt_id, signature_id = uuid.uuid4(), uuid.uuid4()
    for upload_id, kind in [(receipt_id, "receipt"), (signature_id, "signature")]:
        content = image_bytes(signature=kind == "signature")
        key = f"uploads/{upload_id}/normalized.png"
        path = path_for(key)
        path.parent.mkdir(parents=True)
        path.write_bytes(content)
        Upload.objects.create(
            id=upload_id,
            draft=draft,
            kind=kind,
            expense_id=row_id if kind == "receipt" else None,
            original_name=f"{kind}.png",
            source_key=key,
            normalized_key=key,
            size=len(content),
            checksum="synthetic",
            state="ready",
            media_type="image/png",
            pages=1,
        )
    data = {
        "draft_id": str(draft.id),
        "token": token,
        "config_version": event.config_version,
        "full_name": "Alex Beispiel",
        "postal_address": "Teststraße 1\n12345 Berlin\nDeutschland",
        "email": "participant@example.invalid",
        "iban": "DE89 3704 0044 0532 0130 00",
        "purpose": "Teilnahme am synthetischen Workshop",
        "trip_location": "Berlin",
        "trip_start": "2026-10-20",
        "trip_end": "2026-10-21",
        "declaration": True,
        "privacy_acknowledged": True,
        "signature": str(signature_id),
        "expenses": [
            {
                "id": str(row_id),
                "merchant": "Test Rail",
                "purpose": "Workshop-Anreise",
                "receipt_reference": "TEST-1",
                "amount": "12,50",
                "currency": "EUR",
                "uploads": [str(receipt_id)],
            }
        ],
    }
    return {"client": client, "event": event, "draft": draft, "data": data}


@pytest.fixture
def accepted(prepared):
    response = prepared["client"].post(
        "/forms/test/submit/", data=json.dumps(prepared["data"]), content_type="application/json"
    )
    assert response.status_code == 202, response.content
    return Claim.objects.get()


@pytest.fixture
def uploaded_file():
    return SimpleUploadedFile("receipt.png", image_bytes(), content_type="image/png")
