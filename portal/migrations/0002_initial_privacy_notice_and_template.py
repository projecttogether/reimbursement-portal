from decimal import Decimal

from django.db import migrations

from portal.constants import DECLARATION, DECLARATION_VERSION
from portal.privacy_notice import DEFAULT_PRIVACY_NOTICE


def seed(apps, schema_editor):
    if "portal_formtemplate" not in schema_editor.connection.introspection.table_names():
        # Django skips the rewritten 0001 on databases from the event-based release.
        raise RuntimeError(
            "This database was created by the event-based release. Reset the database "
            "and private storage before deploying templates and forms (docs/coolify.md)."
        )
    PrivacyNotice = apps.get_model("portal", "PrivacyNotice")
    FormTemplate = apps.get_model("portal", "FormTemplate")
    TemplateField = apps.get_model("portal", "TemplateField")
    if not PrivacyNotice.objects.exists():
        PrivacyNotice.objects.create(version=1, text=DEFAULT_PRIVACY_NOTICE, created_by="initial")
    if FormTemplate.objects.exists():
        return
    template = FormTemplate.objects.create(
        name="Externe Erstattung",
        description="Auslagen externer Teilnehmender, z. B. für eine Werkstatt.",
        declaration=DECLARATION,
        declaration_version=DECLARATION_VERSION,
        travel="optional",
        mileage_enabled=True,
        mileage_rate=Decimal("0.30"),
    )
    for position, (key, label, required) in enumerate(
        [
            ("kst", "Kostenstelle (KST)", True),
            ("ktr", "Kostenträger (KTR)", False),
            ("id", "ID", False),
        ]
    ):
        TemplateField.objects.create(
            template=template, key=key, label=label, required=required, position=position
        )


class Migration(migrations.Migration):
    dependencies = [("portal", "0001_initial")]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
