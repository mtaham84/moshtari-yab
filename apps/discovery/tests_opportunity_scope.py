"""BUG-07: the admin's list, CSV export and new-count poll must look at the same business."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.businesses.models import Business
from apps.discovery.models import Customer, Opportunity


def _biz(username, opps=0):
    user = get_user_model().objects.create_user(username=username, password="x")
    business = Business.objects.create(user=user, name=username)
    customer = Customer.objects.create(business=business, external_user_id=f"c-{username}")
    for _ in range(opps):
        Opportunity.objects.create(business=business, customer=customer, source_platform="x",
                                   source_raw_message="دنبال کالا هستم")
    return business


class AdminOpportunityScopeTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(username="admin@x.com", password="x")
        self.first = _biz("first@x.com", opps=1)
        self.second = _biz("second@x.com", opps=3)
        self.client.force_login(self.admin)
        self.url = reverse("discovery:opportunity_new_count")

    def test_new_count_respects_selected_business(self):
        res = self.client.get(self.url, {"business_id": self.second.pk, "after": 0})
        self.assertEqual(res.json()["count"], 3)

    def test_new_count_default_matches_list_default(self):
        # without a filter the list shows the first business that has opportunities; the poll must count the same one
        res = self.client.get(self.url, {"after": 0})
        self.assertEqual(res.json()["count"], 1)

    def test_new_count_all_tenants(self):
        res = self.client.get(self.url, {"business_id": "all", "after": 0})
        self.assertEqual(res.json()["count"], 4)

    def test_list_passes_scope_to_poll(self):
        res = self.client.get(reverse("discovery:opportunity_list"), {"business_id": self.second.pk})
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, f'data-query="business_id={self.second.pk}"')
