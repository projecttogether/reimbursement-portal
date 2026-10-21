from django.conf import settings
from django.db import migrations, models


def account_existing_outputs(apps, schema_editor):
    reservations = apps.get_model("portal", "WorkspaceReservation").objects.using(
        schema_editor.connection.alias
    )
    reservations.filter(upload__isnull=False).update(output_bytes=models.F("size"))
    reservations.filter(upload__isnull=True).update(output_bytes=settings.MAX_FINAL_PDF_BYTES)


class Migration(migrations.Migration):
    dependencies = [("portal", "0004_reconciliation_rotation")]

    operations = [
        migrations.AddField(
            model_name="workspacereservation",
            name="output_bytes",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.RunPython(account_existing_outputs, migrations.RunPython.noop),
    ]
