from workers.x_collector.families import Family, family_topic_contexts
from workers.x_collector.intent_queries import product_queries


def test_subscription_brand_spellings_survive_the_six_query_cap(monkeypatch):
    monkeypatch.setattr('workers.x_collector.proqe.load_proqe_queries', lambda _: [])
    family = Family('f1', '9', 'اشتراک هوش مصنوعی', 'دیجیتال > هوش مصنوعی',
                    'فروش اشتراک ChatGPT و Claude', ['1'], titles=['اشتراک هوش مصنوعی'])
    context = family_topic_contexts([family])['f1']
    context['family'] = family
    queries = product_queries(family, terms={'names': ['هوش مصنوعی'], 'problems': ['هزینه هوش مصنوعی']}, topic_context=context)
    text = {q['query'].replace('"', '').removesuffix(' lang:fa') for q in queries}
    assert {'اشتراک کلاد', 'اشتراک Claude', 'اشتراک GPT', 'اشتراک ChatGPT', 'اشتراک جی پی تی'} <= text
    assert len(queries) <= 6


def test_claude_only_subscription_does_not_advertise_gpt(monkeypatch):
    monkeypatch.setattr('workers.x_collector.proqe.load_proqe_queries', lambda _: [])
    family = Family('f1', '9', 'اشتراک Claude', 'دیجیتال > اشتراک برنامه', '', ['1'])
    text = ' '.join(q['query'] for q in product_queries(family))
    assert 'اشتراک کلاد' in text
    assert 'GPT' not in text


def test_beauty_products_never_receive_subscription_queries(monkeypatch):
    monkeypatch.setattr('workers.x_collector.proqe.load_proqe_queries', lambda _: [])
    family = Family('f1', '9', 'کرم پوست', 'زیبایی > پوست', '', ['1'])
    assert not any('اشتراک' in q['query'] for q in product_queries(family))
