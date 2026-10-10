from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("discovery", "0011_opportunityproductmatch_reply_variants")]

    operations = [
        migrations.AddField("customer", "contact_channels", models.JSONField(blank=True, default=list, verbose_name="راه‌های تماس عمومی خوداظهاری‌شده")),
        migrations.AddField("customer", "linked_identities", models.JSONField(blank=True, default=list, verbose_name="هویت‌های پیوندخوردهٔ خوداظهاری‌شده")),
        migrations.AddField("opportunity", "source_posted_at", models.DateTimeField(blank=True, db_index=True, null=True)),
        migrations.AddField("opportunity", "source_posted_at_estimated", models.BooleanField(default=False)),
        migrations.AddField("opportunity", "source_query", models.CharField(blank=True, db_index=True, max_length=255)),
        migrations.AddField("opportunity", "lead_feedback", models.CharField(blank=True, choices=[("good", "مشتری مناسب"), ("bad_not_buyer", "قصد خرید ندارد"), ("bad_seller_or_ad", "فروشنده/تبلیغ"), ("bad_wrong_product", "محصول نامرتبط"), ("bad_too_old", "قدیمی")], max_length=32, null=True)),
        migrations.AddField("opportunity", "lead_feedback_at", models.DateTimeField(blank=True, null=True)),
        migrations.AddField("opportunity", "thread_root_tweet_id", models.BigIntegerField(blank=True, null=True)),
        migrations.AddField("opportunity", "parent_opportunity", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="thread_opportunities", to="discovery.opportunity")),
        migrations.AddField("opportunity", "demand_count", models.PositiveIntegerField(default=0)),
        migrations.AddField("opportunity", "competitor_replies", models.JSONField(blank=True, default=list)),
        migrations.AddField("opportunity", "thread_fetched_at", models.DateTimeField(blank=True, null=True)),
        migrations.CreateModel(
            name="XSearchQuery",
            fields=[("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                    ("text", models.CharField(max_length=100)), ("normalized_text", models.CharField(max_length=100)),
                    ("kind", models.CharField(choices=[(x, x) for x in ("auto_intent", "auto_problem", "auto_product", "manual", "suggested")], default="manual", max_length=20)),
                    ("state", models.CharField(choices=[(x, x) for x in ("active", "paused_manual", "paused_auto", "suggested", "rejected", "stale")], db_index=True, default="active", max_length=20)),
                    ("weight", models.FloatField(default=1.0)), ("state_reason", models.CharField(blank=True, max_length=255)),
                    ("created_at", models.DateTimeField(auto_now_add=True)), ("last_state_change_at", models.DateTimeField(auto_now=True)),
                    ("business", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="x_search_queries", to="businesses.business")),
                    ("product", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="x_search_queries", to="products.product"))],
            options={"constraints": [models.UniqueConstraint(fields=("business", "normalized_text"), name="uniq_x_query_business_normalized")],
                     "indexes": [models.Index(fields=["business", "state"], name="xquery_business_state_idx")]},
        ),
        migrations.CreateModel(
            name="XNegativeTerm",
            fields=[("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                    ("term", models.CharField(max_length=40)), ("normalized_term", models.CharField(max_length=40)), ("created_at", models.DateTimeField(auto_now_add=True)),
                    ("business", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="x_negative_terms", to="businesses.business")),
                    ("product", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="x_negative_terms", to="products.product"))],
            options={"constraints": [models.UniqueConstraint(fields=("business", "product", "normalized_term"), name="uniq_x_negative_term")]},
        ),
        migrations.CreateModel(
            name="XCollectorState",
            fields=[("id", models.PositiveSmallIntegerField(default=1, editable=False, primary_key=True, serialize=False)),
                    ("status", models.CharField(choices=[(x, x) for x in ("idle", "collecting", "rate_limited", "daily_cap", "circuit_open", "auth_failed", "failed", "unknown")], default="unknown", max_length=20)),
                    ("cooldown_until", models.DateTimeField(blank=True, null=True)), ("collected_today", models.PositiveIntegerField(default=0)), ("daily_cap", models.PositiveIntegerField(default=0)),
                    ("last_cycle_at", models.DateTimeField(blank=True, null=True)), ("last_cycle_collected", models.PositiveIntegerField(default=0)),
                    ("failures_last_cycle", models.PositiveIntegerField(default=0)), ("updated_at", models.DateTimeField(auto_now=True))]),
        migrations.CreateModel(
            name="XQueryDaily",
            fields=[("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("day", models.DateField()),
                    ("query_text", models.CharField(max_length=100)), ("fetched", models.PositiveIntegerField(default=0)), ("new_posts", models.PositiveIntegerField(default=0)),
                    ("dropped", models.JSONField(default=dict)), ("analysed", models.PositiveIntegerField(default=0)), ("needs", models.PositiveIntegerField(default=0)),
                    ("opportunities", models.PositiveIntegerField(default=0)), ("cost_toman", models.FloatField(default=0)), ("updated_at", models.DateTimeField(auto_now=True))],
            options={"constraints": [models.UniqueConstraint(fields=("day", "query_text"), name="uniq_x_query_daily")],
                     "indexes": [models.Index(fields=["day", "query_text"], name="xquerydaily_day_query_idx")]},
        ),
        migrations.CreateModel(
            name="XPostFeedback",
            fields=[("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("tweet_id", models.CharField(max_length=40)),
                    ("verdict", models.BooleanField(default=True)), ("created_at", models.DateTimeField(auto_now_add=True)),
                    ("business", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="x_post_feedback", to="businesses.business"))],
            options={"constraints": [models.UniqueConstraint(fields=("business", "tweet_id"), name="uniq_x_post_feedback")]},
        ),
        migrations.CreateModel(
            name="XQueryScore",
            fields=[("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("computed_at", models.DateTimeField(auto_now_add=True)),
                    ("metrics", models.JSONField(default=dict)), ("score", models.FloatField(default=0)), ("recommendation", models.CharField(default="insufficient_data", max_length=40)),
                    ("business", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="x_query_scores", to="businesses.business")),
                    ("query", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="scores", to="discovery.xsearchquery"))]),
    ]
