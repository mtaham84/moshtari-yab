"""A shared X result must pass a fresh wallet gate when it reaches the panel."""
from decimal import Decimal

import pytest

from django.test import TestCase

from apps.billing.models import BillingSettings, Wallet
from apps.discovery.engine_bridge import import_opportunity
from apps.discovery.models import Customer, Opportunity
from apps.discovery.tests import engine_payload, seller
from apps.products.models import Product


class XDeliveryCreditTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _, cls.funded = seller('funded@example.com', 'Funded seller')
        _, cls.other = seller('other@example.com', 'Other seller')
        cls.product = Product.objects.create(business=cls.funded, name='شومیز', description='x', price=100)
        cls.other_product = Product.objects.create(business=cls.other, name='شومیز زنانه', description='x', price=100)

    def setUp(self):
        Wallet.objects.update_or_create(business=self.funded, defaults={'balance_toman': 100})
        Wallet.objects.update_or_create(business=self.other, defaults={'balance_toman': 0})

    def payload(self, identifier, products=None, status='open'):
        result = engine_payload(identifier, products or [self.product.pk], status=status)
        result['source'].update(platform='x', chat_id='x:public')
        return result

    def test_shared_x_lead_only_reaches_funded_seller(self):
        for index, balance in enumerate([0, -10]):
            with self.subTest(balance=balance):
                Wallet.objects.filter(business=self.other).update(balance_toman=balance)
                delivered = import_opportunity(self.payload(f'shared-{index}', [self.product.pk, self.other_product.pk]))
                self.assertEqual([op.business_id for op in delivered], [self.funded.pk])
                self.assertFalse(Opportunity.objects.filter(business=self.other).exists())
                self.assertFalse(Customer.objects.filter(business=self.other).exists())
                self.assertEqual(Wallet.objects.get(business=self.other).balance_toman, Decimal(balance))
        self.assertEqual(Wallet.objects.get(business=self.funded).balance_toman, Decimal(100))

    def test_balance_drained_after_analysis_blocks_delivery(self):
        prepared = self.payload('prepared-while-funded')
        Wallet.objects.filter(business=self.funded).update(balance_toman=0)
        self.assertEqual(import_opportunity(prepared), [])
        self.assertFalse(Opportunity.objects.exists())
        self.assertFalse(Customer.objects.exists())

    def test_topup_allows_future_x_leads_without_pause_flag(self):
        Wallet.objects.filter(business=self.funded).update(balance_toman=0)
        self.assertEqual(import_opportunity(self.payload('before-topup')), [])
        Wallet.objects.filter(business=self.funded).update(balance_toman=100)
        delivered = import_opportunity(self.payload('after-topup'))
        self.assertEqual([op.business_id for op in delivered], [self.funded.pk])

    def test_missing_wallet_does_not_create_wallet_or_deliver(self):
        Wallet.objects.filter(business=self.funded).delete()
        self.assertEqual(import_opportunity(self.payload('missing-wallet')), [])
        self.assertFalse(Wallet.objects.filter(business=self.funded).exists())
        self.assertFalse(Opportunity.objects.exists())

    def test_enforced_minimum_blocks_x_delivery(self):
        BillingSettings.objects.update_or_create(pk=1, defaults={'enforce_balance': True, 'min_balance_toman': 50})
        for balance in [40, 50]:
            with self.subTest(balance=balance):
                Wallet.objects.filter(business=self.funded).update(balance_toman=balance)
                self.assertEqual(import_opportunity(self.payload(f'minimum-{balance}')), [])
        self.assertFalse(Opportunity.objects.exists())

    def test_positive_balance_below_minimum_allowed_when_enforcement_off(self):
        BillingSettings.objects.update_or_create(pk=1, defaults={'enforce_balance': False, 'min_balance_toman': 50})
        Wallet.objects.filter(business=self.funded).update(balance_toman=40)
        self.assertEqual(len(import_opportunity(self.payload('positive'))), 1)

    def test_existing_leads_are_kept_when_new_delivery_is_blocked(self):
        old = import_opportunity(self.payload('already-delivered'))[0]
        Wallet.objects.filter(business=self.funded).update(balance_toman=0)
        self.assertEqual(import_opportunity(self.payload('new-blocked')), [])
        self.assertEqual(list(Opportunity.objects.values_list('pk', flat=True)), [old.pk])

    def test_existing_lead_can_close_while_wallet_is_blocked(self):
        old = import_opportunity(self.payload('close-existing'))[0]
        Wallet.objects.filter(business=self.funded).update(balance_toman=0)
        import_opportunity(self.payload('close-existing', status='resolved'))
        old.refresh_from_db()
        self.assertEqual(old.status, 'RESOLVED')
        self.assertEqual(Opportunity.objects.count(), 1)


# Use a committed wallet and a second PostgreSQL connection to probe the real delivery lock.
@pytest.mark.django_db(transaction=True)
def test_x_delivery_holds_wallet_lock_until_panel_write(monkeypatch):
    import psycopg
    from django.db import connection
    from psycopg.conninfo import make_conninfo
    from apps.discovery import engine_bridge

    _, business = seller('locked@example.com', 'Locked wallet')
    product = Product.objects.create(business=business, name='شومیز', description='x', price=100)
    Wallet.objects.update_or_create(business=business, defaults={'balance_toman': 100})
    params = connection.get_connection_params()
    dsn = make_conninfo(**{key: params[key] for key in ('dbname', 'user', 'password', 'host', 'port') if params.get(key)})
    original_customer = engine_bridge._customer
    def customer_with_debit_probe(*args, **kwargs):
        with psycopg.connect(dsn, autocommit=True) as competing:
            competing.execute("SET lock_timeout = '100ms'")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                competing.execute('UPDATE billing_wallet SET balance_toman=0 WHERE business_id=%s', (business.pk,))
        return original_customer(*args, **kwargs)
    monkeypatch.setattr(engine_bridge, '_customer', customer_with_debit_probe)
    payload = engine_payload('locked-delivery', [product.pk])
    payload['source'].update(platform='x', chat_id='x:public')
    assert len(import_opportunity(payload)) == 1
    assert Wallet.objects.get(business=business).balance_toman == 100
