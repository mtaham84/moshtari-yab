"""Discovery sources: finding candidate pages, fetching a source's posts, retiring sources that never qualify."""
import logging
from datetime import timezone

from django.utils import timezone as dj_timezone

from apps.discovery.ig_scraper import accounts

log = logging.getLogger("instagram.scraper")


def discover_candidate_pages(run, cl, account, business, family, plan, source):
    """Search real public accounts, score their recent posts, and cache at most six family candidates."""
    from datetime import timedelta
    from apps.discovery.instagram_families import INSTAGRAM_PAGE_MIN_COMMENTS
    from apps.discovery.models import InstagramPage

    term = source.label.removeprefix("search:").strip()
    if not term or not hasattr(cl, "search_users"):
        return []
    last_search = business.instagram_crawl_executions.filter(target_type="PAGE", query_text=f"search:{term}",
                                                              crawled_at__gte=dj_timezone.now() - timedelta(days=7)).exists()
    if last_search:
        return []
    try:
        candidates = accounts.instagram_api_call(run, account, cl.search_users, term) or []
    except Exception as exc:
        run.page_search_error = str(exc)
        accounts.note_account_error(run, account, str(exc), cl, exc)
        run.stderr.write(f"Page search failed for {term}: {exc}")
        return []
    terms = ["فروش", "فروشگاه", "عمده", "تولیدی", "کلاس", "آموزش", "آموزشگاه", "کلینیک", "سالن", "مشاوره", "shop", "store", "academy", "clinic"]
    checked, passing = set(), []
    for user in candidates[:8]:
        handle = str(getattr(user, "username", "") or "").strip().lstrip("@")
        uid = str(getattr(user, "pk", "") or getattr(user, "id", ""))
        if not handle or not uid or handle.casefold() in checked:
            continue
        checked.add(handle.casefold())
        if getattr(user, "is_private", None) is not False:
            continue
        bio = str(getattr(user, "biography", "") or "")
        account_name = f"{handle} {getattr(user, 'full_name', '')} {bio}".casefold()
        business_like = bool(getattr(user, "is_business", False)) or any(t in account_name for t in terms)
        if not business_like:
            continue
        try:
            media = accounts.instagram_api_call(run, account, cl.user_medias, uid, amount=6) or []
        except Exception:
            continue
        recent_comments = []
        cutoff = dj_timezone.now() - timedelta(days=14)
        for post in media:
            posted = getattr(post, "taken_at", None)
            if posted and dj_timezone.is_naive(posted):
                posted = dj_timezone.make_aware(posted, timezone.utc)
            if posted and posted < cutoff:
                continue
            recent_comments.append(int(getattr(post, "comment_count", 0) or 0))
        if len(recent_comments) < 2:
            continue
        average = sum(recent_comments) / len(recent_comments) if recent_comments else 0
        if average < INSTAGRAM_PAGE_MIN_COMMENTS:
            continue
        passing.append((average, handle, bio))
    passing.sort(reverse=True)
    created = []
    for rank, (average, handle, bio) in enumerate(passing[:6]):
        page, was_created = InstagramPage.objects.get_or_create(
            business=business, handle=handle,
            defaults={"kind": "watched", "is_active": rank < 2, "last_status": "candidate",
                      "discovered_family_id": family.product_id},
        )
        if was_created:
            created.append(page)
        elif not page.is_active and page.discovered_family_id == family.product_id and page.last_status in {"", "candidate"}:
            page.last_status = "candidate"
            page.save(update_fields=["last_status"])
    return created


def retire_and_delete_source(run, business, source_type: str, label: str, family=None, plan=None):
    """
    Permanently remove an unqualified query so it is never searched again:
    - Removes from InstagramFamilyPlan (hashtags, keywords, page_search_terms)
    - Removes from Product (instagram_terms, instagram_hashtags)
    - Deactivates InstagramPage (is_active=False, last_status='unqualified_retired')
    - Deletes InstagramSourceStat
    """
    from apps.discovery.models import InstagramFamilyPlan, InstagramSourceStat, InstagramPage
    from apps.products.models import Product

    clean_label = str(label or "").strip()
    raw_tag = clean_label.lstrip("#").strip()
    raw_handle = clean_label.lstrip("@").strip()

    # 1. InstagramFamilyPlan
    plans_to_check = [plan] if plan else list(InstagramFamilyPlan.objects.filter(business=business))
    for p in plans_to_check:
        if not p:
            continue
        modified = False
        if source_type == "hashtag":
            old_tags = list(p.hashtags or [])
            new_tags = [t for t in old_tags if str(t).strip().lstrip("#").casefold() != raw_tag.casefold()]
            if len(new_tags) != len(old_tags):
                p.hashtags = new_tags
                modified = True
        elif source_type == "keyword":
            old_kw = list(p.keywords or [])
            new_kw = [k for k in old_kw if str(k).strip().casefold() != clean_label.casefold()]
            if len(new_kw) != len(old_kw):
                p.keywords = new_kw
                modified = True
        elif source_type == "page":
            if clean_label.startswith("search:"):
                term = clean_label[7:].strip()
                old_st = list(p.page_search_terms or [])
                new_st = [s for s in old_st if str(s).strip().casefold() != term.casefold()]
                if len(new_st) != len(old_st):
                    p.page_search_terms = new_st
                    modified = True
        if modified:
            p.save(update_fields=["hashtags", "keywords", "page_search_terms"])

    # 2. Product (instagram_terms, instagram_hashtags)
    if business:
        products = Product.objects.filter(business=business)
        for prod in products:
            prod_mod = False
            if source_type == "hashtag" and prod.instagram_hashtags:
                old_tags = list(prod.instagram_hashtags or [])
                new_tags = [t for t in old_tags if str(t).strip().lstrip("#").casefold() != raw_tag.casefold()]
                if len(new_tags) != len(old_tags):
                    prod.instagram_hashtags = new_tags
                    prod_mod = True
            elif source_type == "keyword" and prod.instagram_terms:
                old_kw = list(prod.instagram_terms or [])
                new_kw = [k for k in old_kw if str(k).strip().casefold() != clean_label.casefold()]
                if len(new_kw) != len(old_kw):
                    prod.instagram_terms = new_kw
                    prod_mod = True
            if prod_mod:
                prod.save(update_fields=["instagram_hashtags", "instagram_terms"])

    # 3. InstagramPage: deactivate auto-discovered pages (discovered_family_id set). The seller's own pages and pages
    # the seller added by hand (no discovered_family_id) stay active. Discovered candidates are created with
    # kind="watched" too, so kind alone cannot tell them apart.
    if source_type == "page":
        page = InstagramPage.objects.filter(business=business, handle__iexact=raw_handle).first()
        if page and (page.kind == "own" or not page.discovered_family_id):
            pass   # the seller's page: keep it
        else:
            InstagramPage.objects.filter(business=business, handle__iexact=raw_handle).update(
                is_active=False,
                last_status="unqualified_retired",
            )

    # 4. InstagramSourceStat (delete)
    InstagramSourceStat.objects.filter(
        business=business,
        source_type=source_type,
        label__iexact=clean_label[:100]
    ).delete()
    if source_type == "hashtag":
        InstagramSourceStat.objects.filter(
            business=business,
            source_type="hashtag",
            label__iexact=raw_tag[:100]
        ).delete()

    # 5. Expand new candidate queries inspired by top-performing queries
    target_plan = plan
    if not target_plan and business:
        target_plan = InstagramFamilyPlan.objects.filter(business=business).first()
    if target_plan:
        try:
            from apps.discovery.instagram_families import expand_queries_from_top_sources
            expanded = expand_queries_from_top_sources(business, target_plan, family=family, retired_label=clean_label)
            if expanded:
                run.stdout.write(run.style.SUCCESS(
                    f"  ✨ [گسترش خودکار کوئری]: کوئری‌های جدید الهام‌گرفته از کوئری‌های برتر اضافه شدند: {expanded}"
                ))
        except Exception as e:
            run.stderr.write(f"Query expansion warning: {e}")


def fetch_source_medias(
    run, cl, account, source_type: str, source_label: str, business=None, page_obj=None,
    target_qualifying: int = 3, min_inspect: int = 15, max_total: int = 30
):
    """
    Fetch candidate medias for a given source_type ('hashtag', 'keyword', 'page').
    - Prefers newest posts (fetches recent first and sorts by taken_at descending).
    - Inspects at least `min_inspect` (15) posts.
    - Seeks at least `target_qualifying` (3) qualifying posts (<= 30 days old and >= 10 comments).
    - If first batch has fewer than 3 qualifying posts, fetches more until >= 3 qualifying found
      or max_total (30) reached or source exhausted.
    """
    from datetime import timedelta
    from apps.discovery.models import InstagramPage
    post_cutoff = dj_timezone.now() - timedelta(days=30)

    all_medias = []
    seen_ids = set()

    def _add(batch):
        for m in batch or []:
            mid = str(getattr(m, "id", "") or getattr(m, "pk", ""))
            if mid and mid not in seen_ids:
                seen_ids.add(mid)
                all_medias.append(m)

    def _count_qualifying():
        cnt = 0
        for m in all_medias:
            t = getattr(m, "taken_at", None)
            if t and dj_timezone.is_naive(t):
                t = dj_timezone.make_aware(t, timezone.utc)
            c_cnt = int(getattr(m, "comment_count", 0) or 0)
            if t and t >= post_cutoff and c_cnt >= 10:
                cnt += 1
        return cnt

    steps = [15, 30]

    if source_type == "hashtag":
        tag = source_label.lstrip("#")
        for amount in steps:
            if len(all_medias) >= max_total:
                break
            try:
                # Prefer recent posts
                recent = accounts.instagram_api_call(run, account, cl.hashtag_medias_recent, tag, amount=amount) or []
                _add(recent)
            except Exception as e:
                run.stderr.write(f"Notice: hashtag_medias_recent for #{tag}: {e}")
                accounts.note_account_error(run, account, str(e), cl, e)

            if _count_qualifying() < target_qualifying or len(all_medias) < min_inspect:
                try:
                    top = accounts.instagram_api_call(run, account, cl.hashtag_medias_top, tag, amount=amount) or []
                    _add(top)
                except Exception:
                    log.debug("hashtag_medias_top for #%s failed", tag, exc_info=True)

            if len(all_medias) >= min_inspect and _count_qualifying() >= target_qualifying:
                break
            if not accounts.request_budget_available(run, account):
                break

    elif source_type == "page":
        handle = source_label.lstrip("@")
        page = page_obj or (InstagramPage.objects.filter(business=business, handle__iexact=handle).first() if business else None)
        target_handle = page.handle if page else handle
        try:
            uid = accounts.instagram_api_call(run, account, cl.user_id_from_username, target_handle)
        except Exception as e:
            run.stderr.write(f"User ID lookup failed for @{target_handle}: {e}")
            accounts.note_account_error(run, account, str(e), cl, e)
            return []

        for amount in steps:
            if len(all_medias) >= max_total:
                break
            try:
                batch = accounts.instagram_api_call(run, account, cl.user_medias, uid, amount=amount) or []
                _add(batch)
            except Exception as e:
                run.stderr.write(f"User medias fetch failed for @{target_handle}: {e}")
                accounts.note_account_error(run, account, str(e), cl, e)
                break
            if len(all_medias) >= min_inspect and _count_qualifying() >= target_qualifying:
                break
            if not accounts.request_budget_available(run, account):
                break

        if page and all_medias:
            page.last_run_at, page.last_status, page.consecutive_failures = dj_timezone.now(), "ok", 0
            page.save(update_fields=["last_run_at", "last_status", "consecutive_failures"])

    elif source_type == "keyword":
        for amount in steps:
            if len(all_medias) >= max_total:
                break
            try:
                batch = accounts.instagram_api_call(run, account, cl.media_search, source_label, amount=amount) or []
                _add(batch)
            except Exception as e:
                run.stderr.write(f"Media search failed for '{source_label}': {e}")
                break
            if len(all_medias) >= min_inspect and _count_qualifying() >= target_qualifying:
                break
            if not accounts.request_budget_available(run, account):
                break

    # Sort all candidate medias by taken_at descending (strictly newest first)
    def _sort_key(m):
        t = getattr(m, "taken_at", None)
        if t is None:
            return dj_timezone.now() - timedelta(days=9999)
        if dj_timezone.is_naive(t):
            t = dj_timezone.make_aware(t, timezone.utc)
        return t

    all_medias.sort(key=_sort_key, reverse=True)
    return all_medias[:max_total]
