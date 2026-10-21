import io
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import close_old_connections, connection, connections
from django.utils import timezone

from portal.event_configuration import event_from_environment
from portal.models import Claim, Draft, Event


@pytest.fixture
def event_environment(monkeypatch):
    values = {
        "EVENT_SLUG": "workshop-a",
        "EVENT_NAME": "Workshop A",
        "EVENT_COST_CENTER": "KST-A",
        "EVENT_COST_OBJECT": "KTR-A",
        "EVENT_RECIPIENT": "finance@example.invalid",
        "EVENT_SUPPORT_EMAIL": "support@example.invalid",
        "EVENT_PRIVACY_NOTICE": "Approved synthetic notice.\nA second paragraph.",
        "EVENT_PRIVACY_VERSION": "approved-v1",
        "EVENT_ACTIVE": "1",
        "EVENT_TRIP_START": "2026-10-20",
        "EVENT_TRIP_END": "2026-10-21",
        "EVENT_OPENS_AT": "2026-10-01T09:00:00+02:00",
        "EVENT_CLOSES_AT": "2026-11-01T09:00:00+01:00",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return values


@pytest.mark.django_db
def test_environment_creates_typed_configuration_and_restart_preserves_revision(event_environment):
    for _ in range(2):
        call_command("configure_event", from_env=True, stdout=io.StringIO())
    event = Event.objects.get()
    assert event.slug == "workshop-a"
    assert event.cost_center == "KST-A"
    assert event.cost_object == "KTR-A"
    assert event.privacy_notice == event_environment["EVENT_PRIVACY_NOTICE"]
    assert event.trip_start == date(2026, 10, 20)
    assert timezone.is_aware(event.opens_at)
    assert event.active and event.travel_required
    assert event.config_version == 1


@pytest.mark.django_db
def test_environment_change_updates_only_owned_event_and_preserves_claim_snapshot(
    event_environment, monkeypatch, accepted
):
    original_snapshot = accepted.snapshot.copy()
    original_version = accepted.event.config_version
    call_command("configure_event", from_env=True, stdout=io.StringIO())
    monkeypatch.setenv("EVENT_SLUG", accepted.event.slug)
    monkeypatch.setenv("EVENT_COST_CENTER", "KST-A-UPDATED")
    monkeypatch.setenv("EVENT_TRIP_START", "")
    call_command("configure_event", from_env=True, stdout=io.StringIO())
    event = Event.objects.get(slug=accepted.event.slug)
    assert event.cost_center == "KST-A-UPDATED" and event.config_version == 2
    assert event.trip_start is None
    accepted.refresh_from_db()
    accepted.event.refresh_from_db()
    assert accepted.snapshot == original_snapshot
    assert accepted.event.config_version == original_version + 1
    assert Event.objects.get(slug="workshop-a").config_version == 1


@pytest.mark.django_db
def test_configuration_restart_preserves_draft_but_changed_values_require_review(
    client, event_environment, monkeypatch
):
    call_command("configure_event", from_env=True, stdout=io.StringIO())
    assert client.get("/forms/workshop-a/").status_code == 200
    draft = Draft.objects.get()
    binding = client.session["draft:workshop-a"]
    call_command("configure_event", from_env=True, stdout=io.StringIO())
    assert client.get("/forms/workshop-a/").status_code == 200
    assert Draft.objects.get().pk == draft.pk
    monkeypatch.setenv("EVENT_COST_CENTER", "KST-CHANGED")
    call_command("configure_event", from_env=True, stdout=io.StringIO())
    response = client.post(
        "/forms/workshop-a/submit/",
        data=json.dumps(
            {
                "draft_id": str(draft.pk),
                "token": binding["token"],
                "config_version": draft.config_version,
            }
        ),
        content_type="application/json",
    )
    assert response.status_code == 409 and not Claim.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "key,value",
    [
        ("EVENT_NAME", ""),
        ("EVENT_SLUG", "invalid slug"),
        ("EVENT_ACTIVE", "yes"),
        ("EVENT_ALLOWED_CURRENCIES", "EUR,INVALID"),
        ("EVENT_TRIP_START", "2026-02-30"),
        ("EVENT_TRIP_START", "2026-10-22"),
        ("EVENT_OPENS_AT", "2026-10-01T09:00:00"),
        ("EVENT_OPENS_AT", "invalid"),
        ("EVENT_CLOSES_AT", "2026-09-01T09:00:00Z"),
        ("EVENT_RECIPIENT", "unapproved@example.invalid"),
        ("EVENT_PRIVACY_VERSION", ""),
    ],
)
def test_invalid_environment_does_not_change_existing_event(
    event_environment, monkeypatch, key, value
):
    call_command("configure_event", from_env=True, stdout=io.StringIO())
    monkeypatch.setenv(key, value)
    with pytest.raises(CommandError):
        call_command("configure_event", from_env=True, stdout=io.StringIO())
    event = Event.objects.get()
    assert event.config_version == 1 and event.cost_center == "KST-A"


def test_environment_defaults_to_closed_and_parses_boolean_currency_settings(event_environment):
    values = event_environment | {
        "EVENT_TRAVEL_REQUIRED": "false",
        "EVENT_ALLOWED_CURRENCIES": "eur, chf",
    }
    values.pop("EVENT_ACTIVE")
    data = event_from_environment(values)
    assert data["active"] is False and data["travel_required"] is False
    assert data["allowed_currencies"] == ["EUR", "CHF"]


@pytest.mark.django_db
def test_production_environment_rejects_active_placeholders(
    event_environment, monkeypatch, settings
):
    settings.DEBUG = False
    monkeypatch.setenv("EVENT_COST_CENTER", "REPLACE_WITH_APPROVED_KST")
    with pytest.raises(CommandError, match="Replace example"):
        call_command("configure_event", from_env=True, stdout=io.StringIO())
    assert not Event.objects.exists()


@pytest.mark.django_db
def test_json_configuration_remains_supported_and_idempotent(event, tmp_path):
    path = tmp_path / "event.json"
    path.write_text(json.dumps({"slug": event.slug, "name": "Renamed workshop"}))
    for _ in range(2):
        call_command("configure_event", str(path), stdout=io.StringIO())
    event.refresh_from_db()
    assert event.name == "Renamed workshop" and event.config_version == 2


@pytest.mark.django_db(transaction=True)
def test_racing_web_startups_create_one_event_revision(event_environment):
    if connection.vendor != "postgresql":
        pytest.skip("Configuration startup serialization requires PostgreSQL.")

    def configure(_):
        close_old_connections()
        try:
            call_command("configure_event", from_env=True, stdout=io.StringIO())
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(configure, range(2)))
    assert Event.objects.count() == 1
    assert Event.objects.get().config_version == 1


@pytest.mark.parametrize(
    "role,extra,accepted",
    [
        (
            "web",
            {"EVENT_SLUG": "workshop-a", "APPROVED_DELIVERY_RECIPIENTS": "ci@example.invalid"},
            True,
        ),
        ("web", {"APPROVED_DELIVERY_RECIPIENTS": "ci@example.invalid"}, False),
        ("webhook", {"MAILERSEND_WEBHOOK_SIGNING_SECRET": "ci-only"}, True),
        ("webhook", {}, False),
        (
            "worker",
            {
                "MAILERSEND_API_TOKEN": "ci-only",
                "MAIL_FROM_ADDRESS": "ci@example.invalid",
                "APPROVED_DELIVERY_RECIPIENTS": "ci@example.invalid",
            },
            True,
        ),
        ("invalid", {}, False),
    ],
)
def test_roles_require_only_the_credentials_they_use(tmp_path, role, extra, accepted):
    environment = {
        "PATH": os.defpath,
        "PRIVATE_STORAGE_ROOT": str(tmp_path / "private"),
        "DEPLOYMENT_ENV": "production",
        "DJANGO_DEBUG": "0",
        "DJANGO_SECRET_KEY": "ci-only-0123456789-abcdefghijklmnopqrstuvwxyz-ABCDEFGHIJKLMNOPQRSTUVWXYZ",
        "DATABASE_URL": "postgresql://ci:ci@localhost/ci",
        "ALLOWED_HOSTS": "ci.example.invalid,127.0.0.1",
        "HEALTHCHECK_TOKEN": "ci-only",
        "PORTAL_ROLE": role,
        "TOTAL_WEB_WORKERS": "6",
        **extra,
    }
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from config import settings; assert settings.UPLOAD_TEMP_HEADROOM_BYTES == 6 * (settings.MAX_RECEIPT_BYTES + 128_000)",
        ],
        env=environment,
        capture_output=True,
        text=True,
    )
    assert (result.returncode == 0) is accepted
