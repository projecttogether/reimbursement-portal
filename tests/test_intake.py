import json
import re

import pytest

from portal.intake import AMOUNT_PATTERN, IntakeError, limits, message, parse_amount
from portal.services.submissions import normalized_payload

pytestmark = pytest.mark.django_db


def rendered_configuration(client, event):
    page = client.get(f"/forms/{event.slug}/").content.decode()
    script = re.search(r'<script id="portal-configuration"[^>]*>(.*?)</script>', page, re.S)
    return page, json.loads(script[1])


def test_client_and_server_share_one_set_of_limits(client, event, settings):
    settings.MAX_FILES_PER_EXPENSE = 2
    settings.MAX_SUBMISSION_BYTES = 20_000_000
    page, config = rendered_configuration(client, event)
    assert config["limits"]["files_per_expense"] == 2
    assert config["limits"] == {
        key: value for key, value in vars(limits()).items() if not key.startswith("_")
    }
    assert config["messages"]["submission_too_large"].endswith("höchstens 20 MB groß sein.")
    assert "20 MB insgesamt" in page and "Bis zu 2 Dateien." in page
    assert f'pattern="{AMOUNT_PATTERN}"' in page
    assert message("files_per_expense") == "Jede Position benötigt 1 bis 2 Belegdateien."


def test_client_learns_currency_precision(client, event):
    event.allowed_currencies = ["EUR", "JPY"]
    event.save()
    _, config = rendered_configuration(client, event)
    assert config["currency_precision"] == {"EUR": 2, "JPY": 0}
    assert parse_amount("1250", "JPY") == 1250
    with pytest.raises(ValueError):
        parse_amount("12,50", "JPY")


def test_server_rejects_rows_beyond_the_configured_file_limit(prepared, settings):
    settings.MAX_FILES_PER_EXPENSE = 1
    data = prepared["data"]
    data["expenses"][0]["uploads"].append(str(data["signature"]))
    with pytest.raises(IntakeError, match="1 bis 1 Belegdateien"):
        normalized_payload(data, prepared["event"])
