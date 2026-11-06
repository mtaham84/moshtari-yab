"""Shared definitions used by the dashboards, the seller panel and services.

Keep one definition per concept so different pages can't disagree about
what a "high intent" lead or a "sent DM" is.
"""

# A lead / opportunity at or above this intent score is "high intent".
HIGH_INTENT_THRESHOLD = 0.8

# Lead statuses that mean the first DM was delivered (later states included).
DM_DELIVERED_STATUSES = ("DM_SENT", "REPLIED", "FOLLOWUP_SENT")
