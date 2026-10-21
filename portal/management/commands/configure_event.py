import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from portal.intake import CURRENCY_PRECISION
from portal.models import Event
from portal.services.delivery import recipient_approved


class Command(BaseCommand):
    help = (
        "Create/update versioned event configuration from JSON. No web administration is exposed."
    )

    def add_arguments(self, parser):
        parser.add_argument("path", nargs="?")
        parser.add_argument("--demo", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        if options["demo"]:
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
                raise CommandError("Provide a JSON configuration path or --demo.")
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
        event = Event.objects.select_for_update().filter(slug=data["slug"]).first()
        event = event or Event(slug=data["slug"])
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
        if event.pk:
            event.config_version += 1
        event.full_clean()
        if event.trip_start and event.trip_end and event.trip_start > event.trip_end:
            raise CommandError("Trip end must not precede trip start.")
        event.save()
        self.stdout.write(f"Configured {event.slug}, version {event.config_version}.")
