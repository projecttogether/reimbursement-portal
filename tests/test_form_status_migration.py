import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


@pytest.mark.django_db(transaction=True)
def test_existing_forms_keep_their_publication_state_when_migrating():
    before = [("portal", "0003_form_primary")]
    after = [("portal", "0004_form_status")]
    executor = MigrationExecutor(connection)
    executor.migrate(before)
    try:
        apps = executor.loader.project_state(before).apps
        template = apps.get_model("portal", "FormTemplate").objects.create(name="Migration")
        Form = apps.get_model("portal", "Form")
        for slug, active in [("open", True), ("closed", False)]:
            Form.objects.create(
                template=template,
                name=slug,
                slug=slug,
                recipient="finance@example.invalid",
                support_email="finance@example.invalid",
                active=active,
            )
        executor = MigrationExecutor(connection)
        executor.migrate(after)
        Form = executor.loader.project_state(after).apps.get_model("portal", "Form")
        assert dict(Form.objects.values_list("slug", "status")) == {
            "open": "published",
            "closed": "draft",
        }
        executor = MigrationExecutor(connection)
        executor.migrate(before)
        Form = executor.loader.project_state(before).apps.get_model("portal", "Form")
        assert dict(Form.objects.values_list("slug", "active")) == {"open": True, "closed": False}
    finally:
        MigrationExecutor(connection).migrate(after)
