import hashlib
import hmac
import json
import uuid

import pytest
from django.utils import timezone

from portal.models import Claim, Delivery, DeliveryAttempt, Draft, Event, Upload
from portal.services.mailersend import WEBHOOK_TEST_SECRET

pytestmark = pytest.mark.django_db


@pytest.fixture
def other_event(event):
    return Event.objects.create(
        slug="other",
        name="Other Workshop",
        cost_center="OTHER-KST",
        recipient=event.recipient,
        support_email=event.support_email,
        privacy_notice=event.privacy_notice,
        privacy_version=event.privacy_version,
    )


def test_web_root_selects_only_its_configured_event(client, event, other_event, settings):
    settings.PORTAL_ROLE = "web"
    settings.EVENT_SLUG = other_event.slug
    response = client.get("/")
    assert response.status_code == 200
    assert "Location" not in response
    assert "csrftoken" in response.cookies
    assert b"Other Workshop" in response.content
    assert b"Test Workshop" not in response.content
    assert client.get("/forms/test/").status_code == 404
    Event.objects.filter(pk=other_event.pk).update(active=False)
    assert client.get("/").status_code == 403


@pytest.mark.parametrize("query", ["", "new=1", "new=1&source=invite%2Fadg"])
def test_web_form_links_redirect_to_root_and_preserve_query(client, event, settings, query):
    settings.PORTAL_ROLE = "web"
    settings.EVENT_SLUG = event.slug
    suffix = f"?{query}" if query else ""
    response = client.get(f"/forms/{event.slug}/{suffix}", follow=True)
    assert response.status_code == 200
    assert response.redirect_chain == [(f"/{suffix}", 302)]
    assert Draft.objects.count() == 1


def test_web_root_resumes_the_same_draft(client, event, settings):
    settings.PORTAL_ROLE = "web"
    settings.EVENT_SLUG = event.slug
    assert client.get("/").status_code == 200
    binding = client.session[f"draft:{event.slug}"]
    assert client.get("/").status_code == 200
    assert client.session[f"draft:{event.slug}"] == binding
    assert Draft.objects.count() == 1


def test_web_can_start_a_new_claim_from_an_old_form_link(client, accepted, settings):
    settings.PORTAL_ROLE = "web"
    settings.EVENT_SLUG = accepted.event.slug
    response = client.get("/")
    assert response.status_code == 302 and response.url == f"/received/{accepted.pk}/"
    response = client.get(f"/forms/{accepted.event.slug}/?new=1", follow=True)
    assert response.status_code == 200
    assert response.redirect_chain == [("/?new=1", 302)]
    assert Draft.objects.filter(event=accepted.event, claim__isnull=True).count() == 1


def test_web_root_does_not_fall_back_to_another_active_event(client, event, settings):
    settings.PORTAL_ROLE = "web"
    settings.EVENT_SLUG = "missing"
    assert client.get("/").status_code == 404
    assert not Draft.objects.exists()


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/forms/test/"),
        ("post", "/forms/test/uploads/"),
        ("get", "/forms/test/uploads/{upload}/status/"),
        ("delete", "/forms/test/uploads/{upload}/"),
        ("post", "/forms/test/submit/"),
    ],
)
def test_other_domain_cannot_read_or_mutate_event_with_a_valid_session(
    prepared, other_event, settings, method, path
):
    upload = Upload.objects.first()
    settings.PORTAL_ROLE = "web"
    settings.EVENT_SLUG = other_event.slug
    response = getattr(prepared["client"], method)(path.format(upload=upload.pk))
    assert response.status_code == 404
    assert Upload.objects.filter(pk=upload.pk).exists()
    assert not Claim.objects.exists()


def test_receipt_is_scoped_even_when_session_cookie_is_replayed(
    client, accepted, other_event, settings
):
    settings.PORTAL_ROLE = "web"
    settings.EVENT_SLUG = accepted.event.slug
    path = f"/received/{accepted.pk}/"
    assert client.get(path).status_code == 200
    settings.EVENT_SLUG = other_event.slug
    assert client.get(path).status_code == 404
    assert client.get(path + "?format=json").status_code == 404


def test_web_services_cannot_receive_provider_callbacks(client, event, settings):
    settings.PORTAL_ROLE = "web"
    settings.EVENT_SLUG = event.slug
    body = json.dumps({"type": "webhook.test"}).encode()
    signature = hmac.new(WEBHOOK_TEST_SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert (
        client.post(
            "/webhooks/mailersend/",
            data=body,
            content_type="application/json",
            headers={"Signature": signature},
        ).status_code
        == 404
    )
    assert client.get("/-/healthz").status_code == 200


@pytest.mark.parametrize("role", ["webhook", "worker"])
def test_shared_services_do_not_expose_intake(client, event, role, settings):
    settings.PORTAL_ROLE = role
    assert client.get("/").status_code == 404
    assert client.get("/forms/test/").status_code == 404
    assert client.post("/forms/test/submit/", {}).status_code == 404
    assert client.get(f"/received/{uuid.uuid4()}/").status_code == 404
    assert client.get("/-/healthz").status_code == 200


def test_one_signed_webhook_updates_deliveries_for_multiple_cost_centers(
    client, event, other_event, settings
):
    settings.PORTAL_ROLE = "webhook"
    settings.MAILERSEND_WEBHOOK_SIGNING_SECRET = "ci-only-signing-secret"
    for index, current_event in enumerate([event, other_event]):
        claim = Claim.objects.create(event=current_event, snapshot=current_event.snapshot())
        delivery = Delivery.objects.create(claim=claim, recipient=current_event.recipient)
        message_id = f"message-{index}"
        DeliveryAttempt.objects.create(
            delivery=delivery, state="provider_accepted", message_id=message_id
        )
        body = json.dumps(
            {
                "type": "activity.delivered",
                "created_at": timezone.now().isoformat(),
                "data": {
                    "id": f"event-{index}",
                    "message_id": message_id,
                    "recipient": delivery.recipient,
                    "tags": [],
                },
            }
        ).encode()
        signature = hmac.new(b"ci-only-signing-secret", body, hashlib.sha256).hexdigest()
        assert (
            client.post(
                "/webhooks/mailersend/",
                data=body,
                content_type="application/json",
                headers={"Signature": signature},
            ).status_code
            == 200
        )
    assert Delivery.objects.filter(state="delivered").count() == 2
    assert {claim.event.cost_center for claim in Claim.objects.all()} == {"TEST-KST", "OTHER-KST"}
