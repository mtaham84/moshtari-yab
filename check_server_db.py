import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.businesses.models import Business, BusinessType
from apps.billing.models import TenantWallet

User = get_user_model()
print("=== BUSINESS TYPES ===")
for bt in BusinessType.objects.all():
    print(f"ID={bt.id}, Key={bt.key}, Name={bt.name}, TreeStatus={bt.tree_status}")

print("=== BUSINESSES & USERS ===")
for b in Business.objects.all().order_by('id'):
    w = TenantWallet.objects.filter(business=b).first()
    bal = w.balance_toman if w else 0
    print(f"BizID={b.id}, UserID={b.user_id}, Email={getattr(b.user, 'email', None)}, Name='{b.name}', Domain='{b.business_domain}', Onboarding={b.onboarding_done}, Slug='{b.slug}', Balance={bal}")

print("=== USERS WITHOUT BUSINESS ===")
for u in User.objects.filter(business=None).order_by('id'):
    print(f"UserID={u.id}, Username={u.username}, Email={u.email}, is_staff={u.is_staff}")
