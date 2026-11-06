"""One-off backfills that used to run as a side effect of GET requests (outreach page, CSV export, ops dashboard)."""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Backfill Instagram lead scores and/or account device profiles."

    def add_arguments(self, parser):
        parser.add_argument("--lead-scores", action="store_true", help="recompute outreach scores of stored leads")
        parser.add_argument("--tenant", default=None, help="only leads of this tenant id (with --lead-scores)")
        parser.add_argument("--device-profiles", action="store_true",
                            help="assign a device profile to accounts that have none")

    def handle(self, *args, **options):
        if not (options["lead_scores"] or options["device_profiles"]):
            self.stderr.write("Nothing to do: pass --lead-scores and/or --device-profiles.")
            return
        if options["lead_scores"]:
            from apps.discovery.instagram_scoring import backfill_all_leads_scores

            total, high = backfill_all_leads_scores(tenant_id=options["tenant"])
            self.stdout.write(f"lead scores: {total} leads updated ({high} with score >= 30)")
        if options["device_profiles"]:
            from apps.discovery.device_emulator import ensure_account_device_profile, has_device_profile
            from apps.discovery.models import InstagramEngineAccount

            assigned = 0
            for account in InstagramEngineAccount.objects.all().iterator():
                if not has_device_profile(account.device_settings):
                    ensure_account_device_profile(account)
                    assigned += 1
            self.stdout.write(f"device profiles: {assigned} accounts updated")
