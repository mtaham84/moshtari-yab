"""Measure Django opportunity and ops pages against a disposable set of 1,000 opportunities.

Run with the same PostgreSQL settings as the panel. All seeded rows are created inside a
transaction that is rolled back after requests finish, so this does not alter persistent data.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("DJANGO_DEBUG", "true")
os.environ.setdefault("DJANGO_SECRET_KEY", "profile-only-local-key")
os.environ.setdefault("PGCONNECT_TIMEOUT", "3")
from cryptography.fernet import Fernet

os.environ.setdefault("FIELD_ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import django
from django.db import OperationalError, connection, transaction
from django.test import Client

django.setup()

from django.contrib.auth import get_user_model
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.businesses.models import Business
from apps.billing.models import AIModel, BillingSettings, Provider, Wallet
from apps.discovery.models import AIAnalysis, Customer, Evidence, Opportunity, OpportunityProductMatch
from apps.products.models import Product


def _fixture(count: int):
    User = get_user_model()
    user = User.objects.create_user(username="profile-seller", email="profile@example.invalid", password="profile-only")
    business = Business.objects.create(user=user, name="Profile seller", business_type="PHYSICAL", business_domain="test")
    product = Product.objects.create(business=business, name="Profile product", description="Generated profile fixture")
    customers = [Customer(business=business, name=f"Buyer {index}", source_platform="telegram") for index in range(count)]
    Customer.objects.bulk_create(customers, batch_size=500)
    customers = list(Customer.objects.filter(business=business).order_by("id"))
    opportunities = [Opportunity(
        business=business, customer=customers[index], source_platform="telegram", source_message_id=str(index),
        source_raw_message=f"Profile message {index}", status="NEW",
    ) for index in range(count)]
    Opportunity.objects.bulk_create(opportunities, batch_size=500)
    opportunities = list(Opportunity.objects.filter(business=business).order_by("id"))
    AIAnalysis.objects.bulk_create([AIAnalysis(
        opportunity=item, need="generated profile need", why_selected="generated profile fixture",
    ) for item in opportunities], batch_size=500)
    OpportunityProductMatch.objects.bulk_create([OpportunityProductMatch(
        opportunity=item, product=product, recommendation_reason="generated profile fixture",
    ) for item in opportunities], batch_size=500)
    Evidence.objects.bulk_create([Evidence(
        opportunity=item, content="generated profile evidence",
    ) for item in opportunities], batch_size=500)
    BillingSettings.objects.get_or_create(pk=1)
    provider, _ = Provider.objects.get_or_create(
        name="Profile provider", defaults={"base_url": "https://example.invalid/v1", "api_key": ""},
    )
    AIModel.objects.get_or_create(provider=provider, name="profile-model", defaults={"is_active": True})
    Wallet.objects.get_or_create(business=business)
    return user, business, opportunities


def _request(client: Client, label: str, path: str) -> dict:
    with CaptureQueriesContext(connection) as queries:
        started = time.perf_counter()
        response = client.get(path)
        elapsed = time.perf_counter() - started
    durations = [float(query.get("time") or 0) for query in queries.captured_queries]
    slow = sorted((query for query in queries.captured_queries if float(query.get("time") or 0) >= 0.05),
              key=lambda query: float(query.get("time") or 0), reverse=True)
    return {
        "page": label,
        "status": response.status_code,
        "query_count": len(queries),
        "request_seconds": round(elapsed, 6),
        "database_seconds": round(sum(durations), 6),
        "slow_queries": [{"seconds": round(float(query.get("time") or 0), 6),
                        "sql": query.get("sql", "").replace("\n", " ")[:500]} for query in slow[:10]],
    }


def profile(count: int) -> list[dict]:
    results = []
    with transaction.atomic():
        user, business, opportunities = _fixture(count)
        client = Client()
        client.force_login(user)
        admin = get_user_model().objects.create_user(
            username="profile-admin", email="admin-profile@example.invalid", password="profile-only", is_staff=True,
        )
        ops_client = Client()
        ops_client.force_login(admin)

        results.append(_request(client, "opportunity_list", reverse("discovery:opportunity_list")))
        results.append(_request(client, "opportunity_detail", reverse("discovery:opportunity_detail", args=[opportunities[0].pk])))
        for label, name in (("ops_dashboard", "ops:dashboard"), ("ops_costs", "ops:costs"),
                            ("ops_funnel", "ops:funnel"), ("ops_providers", "ops:providers"),
                            ("ops_wallets", "ops:wallets"), ("ops_communities", "ops:communities"),
                            ("ops_x", "ops:x"), ("ops_settings", "ops:settings")):
            results.append(_request(ops_client, label, reverse(name)))
        transaction.set_rollback(True)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--opportunities", type=int, default=1000)
    parser.add_argument("--repeat", type=int, default=1)
    args = parser.parse_args()
    try:
        runs = [profile(args.opportunities) for _ in range(args.repeat)]
    except OperationalError as error:
        print(f"Cannot profile Django pages: PostgreSQL is unavailable ({type(error).__name__}).")
        return 2
    for result_set in runs:
        for result in result_set:
            print(f"{result['page']}: HTTP {result['status']} · {result['query_count']} queries · request {result['request_seconds']:.4f}s · SQL {result['database_seconds']:.4f}s")
            for slow in result["slow_queries"]:
                print(f"  slow {slow['seconds']:.4f}s: {slow['sql']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
