from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("communications", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="WhatsAppChannelConfig",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("slug", models.CharField(default="primary", max_length=40, unique=True)),
                ("provider", models.CharField(default="meta_cloud_api", max_length=40)),
                ("partner_name", models.CharField(blank=True, max_length=120)),
                ("display_name", models.CharField(blank=True, max_length=160)),
                ("business_id", models.CharField(blank=True, max_length=120)),
                ("waba_id", models.CharField(blank=True, max_length=120)),
                ("phone_number_id", models.CharField(blank=True, max_length=120)),
                ("display_phone_number", models.CharField(blank=True, max_length=40)),
                ("connection_state", models.CharField(choices=[("not_linked", "Sin vincular"), ("observed", "Observado en Meta"), ("ready", "Listo"), ("blocked", "Bloqueado")], default="not_linked", max_length=24)),
                ("quality_rating", models.CharField(choices=[("unknown", "Sin datos"), ("high", "Alta"), ("medium", "Media"), ("low", "Baja")], default="unknown", max_length=24)),
                ("webhook_state", models.CharField(choices=[("not_configured", "Sin configurar"), ("pending", "Pendiente"), ("verified", "Verificado"), ("error", "Con error")], default="not_configured", max_length=24)),
                ("active", models.BooleanField(default=False)),
                ("updated_by", models.CharField(blank=True, max_length=240)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
    ]
