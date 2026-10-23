from django.db import migrations, models
import django.db.models.deletion
from django.db.models.functions import Lower


class Migration(migrations.Migration):
    dependencies = [
        ("businesses", "0001_initial"),
        ("discovery", "0014_opportunity_seller_feedback"),
        ("products", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="InstagramPage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("handle", models.CharField(max_length=30)),
                ("kind", models.CharField(choices=[("own", "صفحهٔ خودم"), ("watched", "صفحهٔ رقیب")], default="watched", max_length=10)),
                ("is_active", models.BooleanField(default=True)),
                ("last_run_at", models.DateTimeField(blank=True, null=True)),
                ("last_status", models.CharField(blank=True, default="", max_length=24)),
                ("consecutive_failures", models.PositiveSmallIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("business", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="instagram_pages", to="businesses.business")),
                ("default_product", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="instagram_pages", to="products.product")),
            ],
            options={"ordering": ["-is_active", "handle"]},
        ),
        migrations.AddConstraint(
            model_name="instagrampage",
            constraint=models.UniqueConstraint(Lower("handle"), "business", name="uniq_instagram_page_business_handle"),
        ),
    ]
