"""Django's `migrate`, after giving portal's migration 0019 its current name.

Databases that ran 0019 under its earlier name keep that name in django_migrations.
Django would then see 0019 as new and refuse to migrate, since 0020, which depends on
it, has run. Only help texts changed in 0019, so the row takes the current name.
"""

from django.core.management.commands.migrate import Command as MigrateCommand
from django.db import connections
from django.db.migrations.recorder import MigrationRecorder

MIGRATION_0019 = "0019_accent_color_help"
EARLIER_0019 = "0019_accent_follows_"


class Command(MigrateCommand):
    def handle(self, *args, **options):
        recorder = MigrationRecorder(connections[options["database"]])
        if recorder.has_table():
            applied = recorder.migration_qs.filter(app="portal")
            if not applied.filter(name=MIGRATION_0019).exists():
                applied.filter(name__startswith=EARLIER_0019).update(name=MIGRATION_0019)
        return super().handle(*args, **options)
