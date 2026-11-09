"""One discovery cycle: which family targets to scrape, in what order, and scraping each target's sources."""
import logging
import os
import random
import time
from datetime import timedelta

from django.utils import timezone as dj_timezone

from apps.discovery.ig_scraper import accounts, clients, comments, session, sources

log = logging.getLogger("instagram.scraper")


def build_targets(run) -> list[dict]:
    """One target per (business, family) with this cycle's sampled sources. Tenants take turns."""
    from django.conf import settings
    from apps.businesses.models import Business
    from apps.discovery.instagram_families import families_for_business, ensure_family_plan, discover_sources, sample_sources
    by_tenant = []
    # INSTAGRAM_PRIORITY_BUSINESS_IDS first, then every business with active products; without any, all businesses.
    priority_ids = list(settings.INSTAGRAM_PRIORITY_BUSINESS_IDS)
    priority = {b.pk: b for b in Business.objects.filter(pk__in=priority_ids)}
    biz_qs = [priority[pk] for pk in priority_ids if pk in priority]
    biz_qs += [b for b in Business.objects.filter(products__status="ACTIVE", products__is_discovery_active=True)
               .distinct().order_by("id") if b.pk not in priority]
    if not biz_qs:
        biz_qs = list(Business.objects.all().order_by("id"))

    for b in biz_qs:
        family_rows = {}
        tenant_sources = []
        for family in families_for_business(b):
            try:
                plan, built = ensure_family_plan(b, family)
            except Exception as plan_exc:
                log.warning("Family plan failed for business %s / %s", b.pk, family.product_id, exc_info=True)
                run.stderr.write(f"Plan failed for {b.pk}/{family.product_id}: {plan_exc}")
                continue
            if plan is None:
                continue
            run.stdout.write(f"Instagram family plan {family.product_id}: {plan.__dict__}")
            family_sources = discover_sources(b, family, plan)
            family_rows[family.product_id] = (family, plan, family_sources)
            tenant_sources.extend(family_sources)
        selected = sample_sources(
            b, tenant_sources,
            min(4, int(os.getenv("INSTAGRAM_MAX_SOURCES_PER_TENANT_CYCLE", "4"))),
        )
        selected_by_family = {}
        for source in selected:
            selected_by_family.setdefault(source.family_id, []).append(source)
        targets_for_business = []
        for family_id, family_sources in selected_by_family.items():
            family, plan, _ = family_rows[family_id]
            targets_for_business.append({
                "tenant_id": f"tenant_{b.id}", "business": b, "name": b.name,
                "family": family, "plan": plan, "sources": family_sources,
            })
        by_tenant.append(targets_for_business)
    # Tenants take turns first; each tenant then advances one family before the next tenant round.
    return [group[offset] for offset in range(max((len(group) for group in by_tenant), default=0))
            for group in by_tenant if offset < len(group)]


def purge_old_post_hashes(run) -> None:
    """30-day TTL of the crawled-post cache."""
    from apps.discovery.models import InstagramCrawledPost
    thirty_days_ago = dj_timezone.now() - timedelta(days=30)
    try:
        purged_count, _ = InstagramCrawledPost.objects.filter(crawled_at__lt=thirty_days_ago).delete()
        if purged_count > 0:
            run.stdout.write(run.style.NOTICE(f"🧹 [30-Day TTL Cache]: Purged {purged_count} post hashes older than 30 days."))
    except Exception as ttl_exc:
        log.warning("Could not purge old crawled-post hashes", exc_info=True)
        run.stderr.write(f"Notice: 30-day TTL hash cleanup notice: {ttl_exc}")


def scrape_targets(run, scraper_accounts, targets, min_comments) -> int:
    """Scrape every target with a fairly rotated healthy account. Returns the number of leads found."""
    from apps.discovery.anti_ban import InstagramAntiBanManager

    cycle_leads = 0
    for target_idx, target in enumerate(targets):
        # Fair rotating account assignment across targets to ensure all scraper accounts participate
        usable_accounts = [a for a in scraper_accounts if accounts.is_usable(a)]
        if not usable_accounts:
            run.stderr.write(run.style.WARNING(
                f"No healthy scraper account left this cycle; skipping target {target['name']}."
            ))
            continue
        cycle_offset = run.account_offset
        account = usable_accounts[(cycle_offset + target_idx) % len(usable_accounts)]
        run.account_offset = (cycle_offset + 1) % len(usable_accounts)

        run.stdout.write(run.style.SUCCESS(f"\n[Assigning Scraper]: @{account.username} (ID: {account.id}) for target: {target['name']}"))
        cl = clients.client_or_rest(run, account)
        if cl is None:
            continue
        try:
            leads_found = scrape_family_target(run, cl, account, target, min_comments)
            cycle_leads += leads_found
            session.persist_session(run, cl, account)
        except Exception as exc:
            log.warning("Scraping target %s with @%s failed", target["name"], account.username, exc_info=True)
            accounts.note_account_error(run, account, str(exc), cl, exc)
            run.stderr.write(f"Error while scraping target {target['name']}: {exc}")
        finally:
            account.last_action_at = dj_timezone.now()
            account.save(update_fields=["last_action_at"])
        InstagramAntiBanManager.sleep_after_business(run.stdout)
    return cycle_leads


def scrape_family_target(run, cl, account, target, min_comments):
    from apps.discovery.models import InstagramCrawlExecution, InstagramSourceStat
    from apps.discovery.instagram_families import (record_source_result)

    total_leads = 0
    from apps.billing.services import is_blocked
    try:
        if is_blocked(target["business"]):
            run.stdout.write(run.style.WARNING(
                f"Skipping Instagram family for tenant {target['business'].pk}: اعتبار تمام شده"
            ))
            return 0
    except Exception as exc:
        log.warning("Wallet status of business %s unavailable", target["business"].pk, exc_info=True)
        run.stderr.write(f"Skipping Instagram family: wallet status unavailable ({type(exc).__name__})")
        return 0
    page_searches = 0
    selected_report = []
    for source in target["sources"]:
        stat = InstagramSourceStat.objects.filter(business=target["business"], family_id=source.family_id,
                                                   source_type=source.source_type, label=source.label[:100]).first()
        selected_report.append({"type": source.source_type, "label": source.label,
                                "requests": stat.requests if stat else 0, "leads": stat.leads if stat else 0})
    run.stdout.write(f"Selected sources for family {target['family'].product_id}: {selected_report}")
    for source in target["sources"]:
        run.api_requests = 0
        run.page_search_error = ""
        if is_blocked(target["business"]):
            run.stdout.write(run.style.WARNING(
                f"Stopping Instagram sources for tenant {target['business'].pk}: اعتبار تمام شده"
            ))
            break
        if account.status != "ACTIVE":
            fallback_accounts = [a for a in accounts.available_scraper_accounts(run) if a.id != account.id and accounts.is_usable(a)]
            cl = None
            for fallback in fallback_accounts:
                cl = clients.client_or_rest(run, fallback)
                if cl is not None:
                    account = fallback
                    break
            if cl is not None:
                run.stdout.write(run.style.NOTICE(f"🔄 تعویض خودکار اکانت اسکرپر به @{account.username} (اکانت قبلی غیرفعال شد)"))
            else:
                run.stdout.write(run.style.WARNING(f"Stopping sources: اکانت جایگزینی برای @{account.username} در دسترس نیست."))
                break
        if not accounts.request_budget_available(run, account):
            run.stdout.write(run.style.WARNING(f"Stopping sources: سقف درخواست روزانه اکانت @{account.username} تکمیل شد."))
            break
        error = ""
        medias = []
        try:
            if source.source_type == "page" and source.label.startswith("search:"):
                page_searches += 1
                if page_searches > max(1, int(os.getenv("INSTAGRAM_PAGE_SEARCH_TERMS_PER_CYCLE", "1"))):
                    continue
                created = sources.discover_candidate_pages(run, cl, account, target["business"], target["family"],
                                                         target["plan"], source)
                execution_type, query = "PAGE_SEARCH", source.label
                leads = 0
                search_status = "FAILED" if run.page_search_error else "SUCCESS"
                record_source_result(target["business"], source, leads, search_status)
                InstagramCrawlExecution.objects.create(
                    business=target["business"], tenant_id=target["tenant_id"], query_text=query,
                    target_type=execution_type, account_username=account.username, account=account,
                    status=search_status, posts_inspected=0, qualifying_posts=0, leads_found=0,
                    requests=run.api_requests,
                    message=f"صفحه‌های رقیب شناسایی‌شده: {len(created)}",
                )
                continue

            execution_type = {"keyword": "KEYWORD", "hashtag": "HASHTAG", "page": "PAGE"}[source.source_type]
            query = f"#{source.label.lstrip('#')}" if source.source_type == "hashtag" else (f"@{source.label.lstrip('@')}" if source.source_type == "page" else source.label)
            medias = sources.fetch_source_medias(
                run, cl=cl, account=account, source_type=source.source_type, source_label=source.label,
                business=target["business"], page_obj=source.page,
                target_qualifying=3, min_inspect=15, max_total=30
            )
        except Exception as exc:
            log.warning("Fetching source %s (%s) failed", source.label, source.source_type, exc_info=True)
            error = str(exc)
            accounts.note_account_error(run, account, error, cl, exc)
            execution_type = {"keyword": "KEYWORD", "hashtag": "HASHTAG", "page": "PAGE"}[source.source_type]
            query = source.label
        inspected, qualifying, leads, retired = comments.inspect_family_medias(run, cl, account, target, source, medias, min_comments=min_comments)
        total_leads += leads

        if not error and retired and not getattr(source, "manual", False):
            sources.retire_and_delete_source(
                run,
                business=target["business"],
                source_type=source.source_type,
                label=source.label,
                family=target["family"],
                plan=target["plan"]
            )
            run.stdout.write(run.style.ERROR(
                f"  ❌ [حذف دائمی کوئری]: کوئری '{source.label}' ({source.source_type}) به دلیل عدم دستیابی به ۳ پست واجد شرایط پس از بررسی {inspected} پست برای همیشه حذف شد."
            ))
            status = "REMOVED_UNQUALIFIED"
            InstagramCrawlExecution.objects.create(
                business=target["business"], tenant_id=target["tenant_id"], query_text=query,
                target_type=execution_type, account_username=account.username, account=account,
                status=status, posts_inspected=inspected, qualifying_posts=qualifying, leads_found=leads,
                requests=run.api_requests,
                message=f"حذف دائمی کوئری: بررسی {inspected} پست و تنها {qualifying} پست واجد شرایط (حداقل ۳ پست با ۱۰+ کامنت و حداکثر ۳۰ روز عمر). کوئری برای همیشه حذف شد.",
            )
            time.sleep(random.uniform(2.0, 4.0))
            continue

        status = "FAILED" if error and not medias else ("SUCCESS" if qualifying else "NO_POSTS")
        record_source_result(target["business"], source, leads, status)
        InstagramCrawlExecution.objects.create(
            business=target["business"], tenant_id=target["tenant_id"], query_text=query,
            target_type=execution_type, account_username=account.username, account=account,
            status=status, posts_inspected=inspected, qualifying_posts=qualifying, leads_found=leads,
            requests=run.api_requests,
        )
        from apps.discovery.anti_ban import InstagramAntiBanManager
        InstagramAntiBanManager.record_query_action(account, run.stdout)
        InstagramAntiBanManager.sleep_after_query(run.stdout)
    return total_leads
