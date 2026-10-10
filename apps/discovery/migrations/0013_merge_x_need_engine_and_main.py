from django.db import migrations


class Migration(migrations.Migration):
    """Joins main's 0011_community_scope with the X branch (0011_opportunityproductmatch_reply_variants → 0012)."""

    dependencies = [
        ("discovery", "0011_community_scope"),
        ("discovery", "0012_x_growth_fields"),
    ]

    operations = []
