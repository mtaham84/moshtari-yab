import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.contrib.auth import get_user_model
from django.test import RequestFactory
from apps.discovery.views import opportunity_list_view

User = get_user_model()
rf = RequestFactory()

print("\n=== 1. TEST CLOTHING SELLER (clothing-test@peyda.ir) ===")
u_cloth = User.objects.get(email="clothing-test@peyda.ir")
req = rf.get("/discovery/opportunities/", HTTP_HOST="194.60.231.71")
req.user = u_cloth
resp = opportunity_list_view(req)
html = resp.content.decode("utf-8")
print(f"Status: {resp.status_code}")
print("Contains 'علی رضایی':", "علی رضایی" in html)
print("Contains 'پیراهن مردانه نخی':", "پیراهن مردانه نخی" in html)
print("Contains 'دندون پزشکی':", "دندون پزشکی" in html)
print("Contains 'لپ‌تاپ استوک':", "لپ‌تاپ استوک" in html)

print("\n=== 2. TEST ADMIN VIEWING PRODUCT 49 (پیراهن مردانه) ===")
u_admin = User.objects.get(email="taha@peyda.ir")
req_admin = rf.get("/discovery/opportunities/?product_id=49", HTTP_HOST="194.60.231.71")
req_admin.user = u_admin
resp_admin = opportunity_list_view(req_admin)
html_admin = resp_admin.content.decode("utf-8")
print(f"Status: {resp_admin.status_code}")
print("Contains 'علی رضایی':", "علی رضایی" in html_admin)
print("Contains 'نیما باقری':", "نیما باقری" in html_admin)
print("Contains 'دندون پزشکی':", "دندون پزشکی" in html_admin)
print("Contains 'لپ‌تاپ استوک':", "لپ‌تاپ استوک" in html_admin)
print("Contains 'فروشگاه پوشاک نمونه':", "فروشگاه پوشاک نمونه" in html_admin)

print("\n=== 3. TEST CROSS-TENANT ISOLATION (Laptop seller querying Product 49) ===")
u_laptop = User.objects.get(email="seller@moshtariyab.com")
req_laptop = rf.get("/discovery/opportunities/?product_id=49", HTTP_HOST="194.60.231.71")
req_laptop.user = u_laptop
resp_laptop = opportunity_list_view(req_laptop)
html_laptop = resp_laptop.content.decode("utf-8")
print(f"Status: {resp_laptop.status_code}")
print("Contains 'علی رضایی':", "علی رضایی" in html_laptop)
print("Contains 'پیراهن مردانه نخی':", "پیراهن مردانه نخی" in html_laptop)
print("Contains empty state message:", "هیچ فرصتی با فیلترهای جاری پیدا نشد" in html_laptop)
