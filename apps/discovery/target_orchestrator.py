"""
Target scraper orchestration service (P08.14 / R39).

Decouples source iteration, budget checks, crawl execution logging, and query retirement
from the monolithic run_instagram_scraper command.
"""

from __future__ import annotations

import logging
import os
import random
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from django.utils import timezone as dj_timezone

from apps.billing.services import is_blocked
from apps.discovery.family_inspection_service import FamilyInspectionService
from apps.discovery.models import InstagramCrawlExecution, InstagramSourceStat
from apps.discovery.instagram_families import record_source_result

log = logging.getLogger("discovery.target_orchestrator")


class TargetScrapeOrchestrator:
    """
    Coordinates target scraping execution, source iteration, and execution logging.
    """

    def __init__(
        self,
        family_inspector: FamilyInspectionService,
        stdout_writer: Optional[Callable[[str], Any]] = None,
        stderr_writer: Optional[Callable[[str], Any]] = None,
        request_budget_fn: Optional[Callable[[Any], bool]] = None,
        note_error_fn: Optional[Callable[..., bool]] = None,
        retire_source_fn: Optional[Callable[..., Any]] = None,
        discover_pages_fn: Optional[Callable[..., Any]] = None,
        fetch_medias_fn: Optional[Callable[..., Any]] = None,
    ):
        self.family_inspector = family_inspector
        self.stdout_write = stdout_writer or log.info
        self.stderr_write = stderr_writer or log.error
        self.request_budget_available = request_budget_fn or (lambda account: True)
        self.note_account_error = note_error_fn or (lambda account, err, cl=None, exc=None: True)
        self.retire_source = retire_source_fn or (lambda **kwargs: None)
        self.discover_pages = discover_pages_fn or (lambda cl, account, b, f, p, s: [])
        self.fetch_medias = fetch_medias_fn or (lambda **kwargs: [])

    def scrape_family_target(
        self,
        cl: Any,
        account: Any,
        target: Dict[str, Any],
        min_comments: int,
        max_posts: int,
        get_current_requests_fn: Optional[Callable[[], int]] = None,
    ) -> int:
        """
        Scrape sources for a family target with budget checks and execution logging.
        """
        total_leads = 0
        try:
            if is_blocked(target["business"]):
                self.stdout_write(
                    f"Skipping Instagram family for tenant {target['business'].pk}: اعتبار تمام شده"
                )
                return 0
        except Exception as exc:
            self.stderr_write(f"Skipping Instagram family: wallet status unavailable ({type(exc).__name__})")
            return 0

        page_searches = 0
        selected_report = []
        for source in target.get("sources") or []:
            stat = InstagramSourceStat.objects.filter(
                business=target["business"],
                family_id=source.family_id,
                source_type=source.source_type,
                label=source.label[:100],
            ).first()
            selected_report.append({
                "type": source.source_type,
                "label": source.label,
                "requests": stat.requests if stat else 0,
                "leads": stat.leads if stat else 0,
            })
        self.stdout_write(f"Selected sources for family {target['family'].product_id}: {selected_report}")

        for source in target.get("sources") or []:
            current_api_requests = get_current_requests_fn() if get_current_requests_fn else 0
            if is_blocked(target["business"]):
                self.stdout_write(
                    f"Stopping Instagram sources for tenant {target['business'].pk}: اعتبار تمام شده"
                )
                break
            if account.status != "ACTIVE":
                self.stdout_write(f"Stopping sources: اکانت @{account.username} غیرفعال شد.")
                break
            if not self.request_budget_available(account):
                self.stdout_write(f"Stopping sources: سقف درخواست روزانه اکانت @{account.username} تکمیل شد.")
                break

            error = ""
            medias = []
            try:
                if source.source_type == "page" and source.label.startswith("search:"):
                    page_searches += 1
                    if page_searches > max(1, int(os.getenv("INSTAGRAM_PAGE_SEARCH_TERMS_PER_CYCLE", "1"))):
                        continue
                    created = self.discover_pages(
                        cl, account, target["business"], target["family"], target["plan"], source
                    )
                    execution_type, query = "PAGE_SEARCH", source.label
                    leads = 0
                    search_status = "SUCCESS"
                    record_source_result(target["business"], source, leads, search_status)
                    InstagramCrawlExecution.objects.create(
                        business=target["business"],
                        tenant_id=target["tenant_id"],
                        query_text=query,
                        target_type=execution_type,
                        account_username=account.username,
                        account=account,
                        status=search_status,
                        posts_inspected=0,
                        qualifying_posts=0,
                        leads_found=0,
                        requests=current_api_requests,
                        message=f"صفحه‌های رقیب شناسایی‌شده: {len(created)}",
                    )
                    continue

                execution_type = {"keyword": "KEYWORD", "hashtag": "HASHTAG", "page": "PAGE"}[source.source_type]
                query = (
                    f"#{source.label.lstrip('#')}"
                    if source.source_type == "hashtag"
                    else (f"@{source.label.lstrip('@')}" if source.source_type == "page" else source.label)
                )
                medias = self.fetch_medias(
                    cl=cl,
                    account=account,
                    source_type=source.source_type,
                    source_label=source.label,
                    business=target["business"],
                    page_obj=getattr(source, "page", None),
                    target_qualifying=3,
                    min_inspect=15,
                    max_total=30,
                )
            except Exception as exc:
                error = str(exc)
                self.note_account_error(account, error, cl, exc)
                execution_type = {"keyword": "KEYWORD", "hashtag": "HASHTAG", "page": "PAGE"}[source.source_type]
                query = source.label

            inspection_res = self.family_inspector.inspect_family_medias(
                cl, account, target, source, medias, min_comments=min_comments
            )
            inspected, qualifying, leads, retired = inspection_res.as_tuple()
            total_leads += leads

            if not error and retired:
                self.retire_source(
                    business=target["business"],
                    source_type=source.source_type,
                    label=source.label,
                    family=target["family"],
                    plan=target["plan"],
                )
                self.stdout_write(
                    f"  ❌ [حذف دائمی کوئری]: کوئری '{source.label}' ({source.source_type}) به دلیل عدم دستیابی به ۳ پست واجد شرایط پس از بررسی {inspected} پست برای همیشه حذف شد."
                )
                status = "REMOVED_UNQUALIFIED"
                InstagramCrawlExecution.objects.create(
                    business=target["business"],
                    tenant_id=target["tenant_id"],
                    query_text=query,
                    target_type=execution_type,
                    account_username=account.username,
                    account=account,
                    status=status,
                    posts_inspected=inspected,
                    qualifying_posts=qualifying,
                    leads_found=leads,
                    requests=current_api_requests,
                    message=f"حذف دائمی کوئری: بررسی {inspected} پست و تنها {qualifying} پست واجد شرایط (حداقل ۳ پست با ۱۰+ کامنت و حداکثر ۳۰ روز عمر). کوئری برای همیشه حذف شد.",
                )
                time.sleep(random.uniform(2.0, 4.0))
                continue

            status = "FAILED" if error and not medias else ("SUCCESS" if qualifying else "NO_POSTS")
            record_source_result(target["business"], source, leads, status)
            InstagramCrawlExecution.objects.create(
                business=target["business"],
                tenant_id=target["tenant_id"],
                query_text=query,
                target_type=execution_type,
                account_username=account.username,
                account=account,
                status=status,
                posts_inspected=inspected,
                qualifying_posts=qualifying,
                leads_found=leads,
                requests=current_api_requests,
                message=f"خانواده {target['family'].product_id}: بررسی {inspected} پست ({qualifying} واجد شرایط ۱۰+ کامنت و ۳۰ روز)؛ {leads} سرنخ. {error[:180]}",
            )
            time.sleep(random.uniform(3.0, 7.0))

        return total_leads
