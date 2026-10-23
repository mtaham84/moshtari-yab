from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("businesses", "0001_initial"),
        ("discovery", "0016_instagram_collection_state"),
        ("products", "0008_alter_product_url"),
    ]

    operations = [
        migrations.CreateModel(
            name="InstagramCategoryTarget",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("terms", models.JSONField(blank=True, default=list)),
                ("hashtags", models.JSONField(blank=True, default=list)),
                ("max_posts", models.PositiveSmallIntegerField(default=5)),
                ("window_days", models.PositiveSmallIntegerField(default=7)),
                ("is_active", models.BooleanField(default=False)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("business", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="instagram_category_targets", to="businesses.business")),
                ("category", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="instagram_targets", to="products.category")),
            ],
            options={"ordering": ["category__full_path"]},
        ),
        migrations.AddConstraint(
            model_name="instagramcategorytarget",
            constraint=models.UniqueConstraint(fields=("business", "category"), name="uniq_ig_category_target"),
        ),
    ]
