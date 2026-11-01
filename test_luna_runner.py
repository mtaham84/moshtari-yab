import os, django, json, time
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()
from need_engine.config import EngineConfig
from need_engine.store import Store
from need_engine.llm import LLMClient

cfg = EngineConfig()
store = Store(cfg.database_url, cfg.state_schema)
llm = LLMClient(cfg, store)

print("Registry candidates for 'extract':", llm.registry.candidate_models_for("extract"))
print("Registry endpoint for 'code/gpt-6-luna':", llm.registry.endpoint("code/gpt-6-luna")[0])
print("Registry endpoint for 'gpt-6-luna':", llm.registry.endpoint("gpt-6-luna")[0])

print("\n--- Testing complete_json with stage='x_query_extract', model='code/gpt-6-luna' ---")
t0 = time.time()
try:
    data, usage = llm.complete_json("x_query_extract", "code/gpt-6-luna", "Return JSON: {\"ok\": true}", "test")
    print("SUCCESS in %.2fs: %s | usage: %s" % (time.time() - t0, data, usage))
except Exception as e:
    print("FAILED in %.2fs: %s" % (time.time() - t0, e))

print("\n--- Testing complete_json with stage='x_query_extract', model='gpt-6-luna' ---")
t0 = time.time()
try:
    data, usage = llm.complete_json("x_query_extract", "gpt-6-luna", "Return JSON: {\"ok\": true}", "test")
    print("SUCCESS in %.2fs: %s | usage: %s" % (time.time() - t0, data, usage))
except Exception as e:
    print("FAILED in %.2fs: %s" % (time.time() - t0, e))

store.close()
