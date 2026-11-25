"""`migrate` gives migration 0019 its current name in databases that ran it under an
earlier one (portal/management/commands/migrate.py)."""

import pytest
from django.core.management import call_command
from django.db import connection
from django.db.migrations.recorder import MigrationRecorder

CURRENT = "0019_accent_color_help"


def portal_0019():
    rows = MigrationRecorder(connection).migration_qs.filter(app="portal", name__startswith="0019")
    return sorted(rows.values_list("name", flat=True))


@pytest.mark.django_db(transaction=True)
def test_migrate_takes_over_0019_under_its_earlier_name():
    MigrationRecorder(connection).migration_qs.filter(app="portal", name=CURRENT).update(
        name="0019_accent_follows_earlier_colors"
    )
    # Without the new name, Django refuses: 0020 ran before its dependency 0019.
    call_command("migrate", verbosity=0)
    assert portal_0019() == [CURRENT]


@pytest.mark.django_db(transaction=True)
def test_migrate_leaves_the_current_name_alone():
    call_command("migrate", verbosity=0)
    assert portal_0019() == [CURRENT]
