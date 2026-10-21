import hashlib
import json
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from portal.event_configuration import event_from_environment
from portal.intake import CURRENCY_PRECISION
from portal.models import Event
from portal.services.delivery import recipient_approved


class Command(BaseCommand):
    help = "Create/update versioned event configuration from environment variables or JSON."

    def add_arguments(self, parser):
        parser.add_argument("path", nargs="?")
        source = parser.add_mutually_exclusive_group()
        source.add_argument("--demo", action="store_true")
        source.add_argument("--from-env", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        if options["path"] and (options["demo"] or options["from_env"]):
            raise CommandError("Choose exactly one configuration source.")
        if options["from_env"]:
            data = event_from_environment()
            if settings.PORTAL_ROLE == "web" and data["slug"] != settings.EVENT_SLUG:
                raise CommandError("Event configuration must match this web service's EVENT_SLUG.")
        elif options["demo"]:
            if not settings.DEBUG or settings.MAIL_DELIVERY_MODE != "local":
                raise CommandError("Demo configuration is allowed only in local development.")
            data = {
                "slug": "demo",
                "name": "ProjectTogether Workshop",
                "cost_center": "DEMO",
                "cost_object": "DEMO-2026",
                "recipient": "finance@example.invalid",
                "support_email": "finance@example.invalid",
                "instructions": "Reiche hier deine Auslagen für den Workshop ein. Bitte füge für jede Position alle zugehörigen Belegseiten hinzu.",
                "privacy_notice": "Beispielhinweis für die lokale Testumgebung. Deine Angaben werden zur Bearbeitung des Erstattungsantrags, zur PDF-Erstellung und zum Versand an Finance verwendet. Vor dem Produktivbetrieb ist hier der freigegebene Datenschutzhinweis zu hinterlegen.",
                "privacy_version": "demo-only",
                "travel_required": True,
            }
        else:
            if not options["path"]:
                raise CommandError("Provide --from-env, a JSON configuration path, or --demo.")
            try:
                data = json.loads(Path(options["path"]).read_text())
            except (OSError, ValueError) as exc:
                raise CommandError("The configuration must be a readable JSON file.") from exc
        if not isinstance(data, dict) or not data.get("slug"):
            raise CommandError("Configuration must include a slug.")
        allowed_fields = {field.name for field in Event._meta.fields} - {"id", "config_version"}
        if set(data) - allowed_fields:
            raise CommandError("Unknown configuration fields.")
        currencies = data.get("allowed_currencies", ["EUR"])
        if (
            not isinstance(currencies, list)
            or not currencies
            or any(code not in CURRENCY_PRECISION for code in currencies)
        ):
            raise CommandError("Use supported ISO currencies: EUR, CHF, GBP, USD, JPY.")
        # A new event has no row to lock. Serialize same-slug startup requests,
        # including first creation, without blocking other web deployments.
        if connection.vendor == "postgresql":
            lock = int.from_bytes(
                hashlib.sha256(str(data["slug"]).encode()).digest()[:8], signed=True
            )
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", [lock])
        event = Event.objects.select_for_update().filter(slug=data["slug"]).first()
        event = event or Event(slug=data["slug"])
        previous = {field: getattr(event, field) for field in allowed_fields}
        for key, value in data.items():
            setattr(event, key, value)
        if not recipient_approved(event.recipient):
            raise CommandError("Event recipient must be in APPROVED_DELIVERY_RECIPIENTS.")
        if not settings.DEBUG and event.privacy_version == "demo-only":
            raise CommandError("Production requires an approved privacy notice.")
        if (
            event.active
            and not settings.DEBUG
            and any(
                "REPLACE_WITH" in str(getattr(event, name))
                for name in ["cost_center", "cost_object", "privacy_notice", "privacy_version"]
            )
        ):
            raise CommandError(
                "Replace example configuration values before opening a production event."
            )
        try:
            event.full_clean()
        except ValidationError as exc:
            fields = ", ".join(sorted(exc.message_dict))
            raise CommandError(f"Invalid event configuration fields: {fields}.") from exc
        if event.trip_start and event.trip_end and event.trip_start > event.trip_end:
            raise CommandError("Trip end must not precede trip start.")
        if event.opens_at and event.closes_at and event.opens_at >= event.closes_at:
            raise CommandError("Closing time must be after opening time.")
        changed = not event.pk or any(
            previous[field] != getattr(event, field) for field in allowed_fields
        )
        if changed:
            if event.pk:
                event.config_version += 1
            event.save()
        self.stdout.write(f"Configured {event.slug}, version {event.config_version}.")
