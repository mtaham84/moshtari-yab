
import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()
from apps.billing.models import AIModel, Provider

print("=== PROVIDERS ===")
for p in Provider.objects.all():
    print(f"Provider {p.id}: {p.name}, base_url={p.base_url}, api_key={p.api_key[:8]}..., active={p.is_active}")

print("=== MODELS ===")
for m in AIModel.objects.all():
    print(f"Model {m.id}: {m.name}, role={m.role}, active={m.is_active}, priority={m.priority}, prov={m.provider.name if m.provider else None}")

print("=== ENV LLM SETTINGS ===")
for k, v in os.environ.items():
    if "LLM" in k or "MODEL" in k or "LUNA" in k or "GEMINI" in k:
        print(f"{k} = {v}")
