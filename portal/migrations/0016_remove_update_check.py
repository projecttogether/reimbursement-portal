from django.db import migrations

# The worker no longer checks for new versions or records its checks.


class Migration(migrations.Migration):
    dependencies = [("portal", "0015_update_check")]

    operations = [migrations.DeleteModel(name="UpdateCheck")]
