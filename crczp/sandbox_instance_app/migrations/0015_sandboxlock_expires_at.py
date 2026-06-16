from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sandbox_instance_app', '0014_sandboxlock_created'),
    ]

    operations = [
        migrations.AddField(
            model_name='sandboxlock',
            name='expires_at',
            field=models.DateTimeField(default=None, null=True),
        ),
    ]
