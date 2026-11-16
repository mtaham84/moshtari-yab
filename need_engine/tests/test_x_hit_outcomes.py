from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from need_engine.config import EngineConfig
from need_engine.engine import NeedEngine
from need_engine.extract import extract_x_product_batch
from need_engine.schemas import ChatMessage


@pytest.mark.parametrize('cached', [False, True])
@pytest.mark.parametrize('fit,has_need,emitted,outcome', [
    ('yes', True, True, 'customer'),
    ('no', True, False, 'not_fit'),
    ('partly', True, False, 'not_fit'),
    ('no', False, False, 'not_customer'),
])
def test_product_hit_outcomes_including_cache(cached, fit, has_need, emitted, outcome, monkeypatch):
    now = datetime.now(timezone.utc)
    message = ChatMessage(chat_id='x:product:p1', message_id=101, author_id='buyer',
                          text='دنبال خرید لپتاپ هستم', date=now, platform='x')
    needs = [{'author_id': 'a1', 'evidence_message_ids': [1], 'fit': fit,
              'label': 'explicit_need', 'is_opportunity': True, 'buyer_intent_confirmed': True,
              'promotional_content': False, 'need': 'لپتاپ', 'solution_queries': ['لپتاپ'],
              'strength': 'strong'}] if has_need else []
    cache = {}
    rows = []
    store = SimpleNamespace(cache_get=cache.get, cache_put=lambda k, v: cache.update({k: v}),
                            emitted=lambda _: emitted, record_x_hit_results=rows.extend)
    llm = SimpleNamespace(store=store, complete_json=lambda *a, **kw: ({'needs': needs}, {}))
    monkeypatch.setattr('need_engine.extract.get_system_prompt', lambda key, default: default)
    args = ([message], message.chat_id, {'product_id': 'p1'}, llm, EngineConfig(), now)
    if cached:
        extract_x_product_batch(*args)
        # Old cached empty results still carry the default "no" fit.
        llm.complete_json = lambda *a, **kw: pytest.fail('cache should avoid LLM')
    cards, _, fits = extract_x_product_batch(*args)
    engine = NeedEngine.__new__(NeedEngine)
    engine.store = store
    engine._record_x_hits([message], {101: cards[0]} if cards else {}, fits)
    assert [row[2] for row in rows] == [outcome]
