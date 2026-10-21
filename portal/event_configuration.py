"""Parse the event owned by one web deployment without touching shared state."""

import os

from django.core.management.base import CommandError
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from portal.constants import DECLARATION, DECLARATION_VERSION


def event_from_environment(environ=None):
    environ = os.environ if environ is None else environ
    fields = {
        "slug": "EVENT_SLUG",
        "name": "EVENT_NAME",
        "cost_center": "EVENT_COST_CENTER",
        "recipient": "EVENT_RECIPIENT",
        "support_email": "EVENT_SUPPORT_EMAIL",
        "privacy_notice": "EVENT_PRIVACY_NOTICE",
        "privacy_version": "EVENT_PRIVACY_VERSION",
    }
    missing = [variable for variable in fields.values() if not environ.get(variable, "").strip()]
    if missing:
        raise CommandError(f"Missing event environment variables: {', '.join(missing)}")
    data = {field: environ[variable].strip() for field, variable in fields.items()}
    for field in ["cost_object", "finance_note", "instructions"]:
        data[field] = environ.get(f"EVENT_{field.upper()}", "").strip()
    data["declaration"] = environ.get("EVENT_DECLARATION", DECLARATION).strip()
    data["declaration_version"] = environ.get(
        "EVENT_DECLARATION_VERSION", DECLARATION_VERSION
    ).strip()
    data["allowed_currencies"] = [
        code.strip().upper() for code in environ.get("EVENT_ALLOWED_CURRENCIES", "EUR").split(",")
    ]
    for field, default in [("active", "0"), ("travel_required", "1")]:
        variable = f"EVENT_{field.upper()}"
        value = environ.get(variable, default).strip().lower()
        if value not in {"0", "1", "false", "true"}:
            raise CommandError(f"{variable} must be 0, 1, false, or true.")
        data[field] = value in {"1", "true"}
    for field in ["trip_start", "trip_end", "opens_at", "closes_at"]:
        variable = f"EVENT_{field.upper()}"
        value = environ.get(variable, "").strip()
        parsed = None
        if value:
            try:
                parsed = parse_datetime(value) if field.endswith("_at") else parse_date(value)
            except ValueError:
                pass
            if parsed is None or (field.endswith("_at") and timezone.is_naive(parsed)):
                raise CommandError(
                    f"{variable} must be an ISO date or a timestamp with a timezone, as appropriate."
                )
        data[field] = parsed
    return data
