"""BUG-08: one Customer per (business, platform, external id); the phone update uses the Instagram identity."""
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from apps.businesses.models import Business
from apps.discovery.models import Customer, InstagramEngineLead


class CustomerIdentityTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="phone@x.com", password="x")
        self.business = Business.objects.create(user=self.user, name="کلینیک")

    def test_duplicate_identity_is_rejected(self):
        Customer.objects.create(business=self.business, source_platform="instagram", external_user_id="42")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Customer.objects.create(business=self.business, source_platform="instagram", external_user_id="42")

    def test_same_id_on_other_platform_and_blank_ids_are_allowed(self):
        Customer.objects.create(business=self.business, source_platform="instagram", external_user_id="42")
        Customer.objects.create(business=self.business, source_platform="telegram", external_user_id="42")
        Customer.objects.create(business=self.business, source_platform="telegram", external_user_id="")
        Customer.objects.create(business=self.business, source_platform="telegram", external_user_id="")
        self.assertEqual(Customer.objects.count(), 4)

    def test_phone_update_does_not_touch_telegram_customer_with_same_id(self):
        telegram = Customer.objects.create(business=self.business, source_platform="telegram", external_user_id="555")
        lead = InstagramEngineLead.objects.create(tenant_id=f"tenant_{self.business.pk}", username="buyer",
                                                  ig_user_pk="555", comment_text="?")
        self.client.force_login(self.user)
        res = self.client.post(reverse("accounts:update_lead_phone"), {"lead_id": lead.pk, "phone_number": "09123456789"})
        self.assertEqual(res.status_code, 200, res.content)
        telegram.refresh_from_db()
        self.assertFalse(telegram.phone_number)
        ig = Customer.objects.get(business=self.business, source_platform="instagram", external_user_id="555")
        self.assertEqual(ig.phone_number, "09123456789")
