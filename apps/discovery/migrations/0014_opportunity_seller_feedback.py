from django.db import migrations, models
from django.utils.dateparse import parse_datetime


def copy_existing_feedback(apps, schema_editor):
    Opportunity = apps.get_model("discovery", "Opportunity")
    for opportunity in Opportunity.objects.filter(lead_feedback__in=[
        "good", "bad_not_buyer", "bad_seller_or_ad", "bad_wrong_product", "bad_too_old"
    ]).iterator():
        if opportunity.lead_feedback == "good":
            feedback, reason = "relevant", ""
        else:
            feedback = "irrelevant"
            reason = {
                "bad_not_buyer": "not_a_buyer",
                "bad_wrong_product": "wrong_product",
                "bad_seller_or_ad": "spam",
            }.get(opportunity.lead_feedback, "")
        opportunity.feedback = feedback
        opportunity.feedback_reason = reason
        opportunity.feedback_at = opportunity.lead_feedback_at
        opportunity.save(update_fields=["feedback", "feedback_reason", "feedback_at"])

    for opportunity in Opportunity.objects.filter(feedback="").iterator():
        metadata = opportunity.trace_metadata or {}
        feedback = metadata.get("seller_feedback")
        if feedback not in {"relevant", "irrelevant"}:
            continue
        opportunity.feedback = feedback
        opportunity.feedback_at = parse_datetime(metadata["seller_feedback_at"]) if metadata.get("seller_feedback_at") else None
        opportunity.feedback_reason = metadata.get("seller_feedback_reason", "")
        opportunity.save(update_fields=["feedback", "feedback_at", "feedback_reason"])


class Migration(migrations.Migration):
    dependencies = [("discovery", "0013_merge_x_need_engine_and_main")]

    operations = [
        migrations.AddField(
            model_name="opportunity",
            name="feedback",
            field=models.CharField(blank=True, choices=[("", "ثبت نشده"), ("relevant", "مشتری واقعی"), ("irrelevant", "نامرتبط")], db_index=True, default="", max_length=16),
        ),
        migrations.AddField(
            model_name="opportunity",
            name="feedback_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="opportunity",
            name="feedback_reason",
            field=models.CharField(blank=True, choices=[("not_a_buyer", "قصد خرید نداشت"), ("wrong_product", "محصول اشتباه بود"), ("already_bought", "قبلاً خریده بود"), ("spam", "هرزنامه یا تبلیغ")], default="", max_length=32),
        ),
        migrations.RunPython(copy_existing_feedback, migrations.RunPython.noop),
    ]
