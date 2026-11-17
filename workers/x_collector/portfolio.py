"""Persistent seller portfolios in the existing panel query/score tables, consumed through SQL."""
from datetime import datetime, timedelta, timezone
import logging

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from need_engine.text import norm
from shared.x_query_metrics import weekly_query_metrics
from .families import family_topic_contexts, family_phrase_allowed
from .intent_queries import clean_term, operators, positive_word_count, _quote
from .portfolio_rules import MIN_REVIEWED_POSTS, replacement_plan, weekly_scores
from .config import query_limit

log = logging.getLogger(__name__)
VERSION = 'weekly-v1'


def read_metrics(db, config, product_ids, now):
    try:
        with db.transaction():
            return weekly_query_metrics(lambda sql, params: db.execute(sql, params).fetchall(),
                                        config.crawler_schema, config.state_schema, product_ids, now - timedelta(days=7))
    except psycopg.errors.UndefinedTable:
        log.warning('X weekly evidence tables are not ready; retaining the initial portfolio', exc_info=True)
        return {'queries': {}, 'reviewed': 0, 'watermark': None}


def _insert(db, bid, family, query, kind, now):
    if len(query) > 100:
        return
    return db.execute('''INSERT INTO public.discovery_xsearchquery
        (business_id,product_id,category_id,text,normalized_text,kind,state,weight,generation_version,
         domain,state_reason,created_at,last_state_change_at)
        VALUES (%s,%s,NULL,%s,%s,%s,'active',0.5,%s,%s,'',%s,%s)
        ON CONFLICT (business_id,normalized_text) DO NOTHING RETURNING id''',
               (bid, int(family.member_product_ids[0]), query, norm(query), 'auto_' + ('problem' if kind == 'problem' else 'intent'),
                VERSION, str(family.product_id), now, now)).fetchone()


def _active(db, bid):
    return db.execute('''SELECT id,text AS query,domain,weight,state FROM public.discovery_xsearchquery
        WHERE business_id=%s AND generation_version=%s AND state='active' ORDER BY id''', (bid, VERSION)).fetchall()


def _prepare_candidates(raw, families, contexts, historical):
    result, seen = [], {norm(q) for q in historical}
    for item in raw:
        domain = str(item.get('domain', ''))
        if domain not in families:
            continue
        phrase = clean_term(item.get('query', ''))
        context = contexts[domain]
        if not 2 <= len(phrase.split()) <= 3 or not family_phrase_allowed(
                phrase, context['anchor_tokens'], context['other_family_nouns'], context.get('topic_tokens')):
            continue
        query = f'{_quote(phrase)} {operators()}'.strip()
        if len(query) > 100 or norm(query) in seen:
            continue
        seen.add(norm(query))
        result.append({'query': query, 'domain': domain, 'kind': item.get('kind', 'problem')})
    return result


def _evaluate(db, config, bid, families, rows, metrics, generator, now, required):
    last = db.execute('''SELECT s.computed_at,s.metrics FROM public.discovery_xqueryscore s
        JOIN public.discovery_xsearchquery q ON q.id=s.query_id
        WHERE s.business_id=%s AND q.generation_version=%s ORDER BY s.computed_at DESC,s.id DESC LIMIT 1''',
                      (bid, VERSION)).fetchone()
    watermark = metrics.get('watermark')
    if last and (now - last['computed_at'] < timedelta(days=1) or last['metrics'].get('watermark') == watermark):
        return
    scored = [{**r, **metrics['queries'].get(r['query'], {}), 'required': r['query'] in required} for r in rows]
    rate = weekly_scores(scored)
    plan = []
    if metrics['reviewed'] >= MIN_REVIEWED_POSTS:
        provisional = replacement_plan(scored, metrics['reviewed'], [{'query': '__pending__' + str(i), 'domain': r['domain']}
                                       for i, r in enumerate(scored)])
        if provisional:
            winners = sorted(scored, key=lambda r: (-rate[r['query']], r['query']))[:5]
            history = [r['text'] for r in db.execute('SELECT text FROM public.discovery_xsearchquery WHERE business_id=%s', (bid,))]
            raw = []
            for domain in sorted({r['domain'] for _, r in provisional}):
                raw.extend({**r, 'domain': domain} for r in generator(config, families[domain], winners, history))
            candidates = _prepare_candidates(raw, families, family_topic_contexts(list(families.values())), history)
            plan = replacement_plan(scored, metrics['reviewed'], candidates)
    for old, candidate in plan:
        family = families[candidate['domain']]
        if not _insert(db, bid, family, candidate['query'], candidate['kind'], now):
            raise RuntimeError('X replacement query already exists; rolling back the entire cycle')
        db.execute('''UPDATE public.discovery_xsearchquery SET state='archived',state_reason='weekly_replaced',
            last_state_change_at=%s WHERE business_id=%s AND generation_version=%s AND text=%s''', (now, bid, VERSION, old))
    if plan and len(_active(db, bid)) != len(rows):
        raise RuntimeError('X portfolio replacement must preserve the active query count')
    for row in rows:
        db.execute('''INSERT INTO public.discovery_xqueryscore (business_id,query_id,computed_at,metrics,score,recommendation)
            VALUES (%s,%s,%s,%s,%s,%s)''', (bid, row['id'], now,
            Jsonb({'reviewed_posts': metrics['reviewed'], 'watermark': watermark, 'window_days': 7,
                   **metrics['queries'].get(row['query'], {})}), rate[row['query']],
            'replaced' if any(old == row['query'] for old, _ in plan) else 'retained'))


def manage_portfolio(config, families, candidates, *, generator=None, now=None, enabled_businesses=None):
    """One advisory-locked transaction per seller; common text is still one shared X search."""
    now = now or datetime.now(timezone.utc)
    if not families:
        return []
    generator = generator or _generate
    by_seller = {}
    for family in families:
        by_seller.setdefault(str(family.business_id), {})[str(family.product_id)] = family
    merged = {}
    with psycopg.connect(config.database_url, row_factory=dict_row) as db:
        for bid, owned in by_seller.items():
            with db.transaction():
                db.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', ('x_portfolio:' + bid,))
                db.execute('''UPDATE public.discovery_xsearchquery SET state='stale',state_reason='category_inactive',
                    last_state_change_at=%s WHERE business_id=%s AND generation_version=%s AND state='active'
                    AND NOT (domain=ANY(%s))''', (now, bid, VERSION, list(owned)))
                all_rows = db.execute('''SELECT domain FROM public.discovery_xsearchquery
                    WHERE business_id=%s AND generation_version=%s''', (bid, VERSION)).fetchall()
                known = {r['domain'] for r in all_rows}
                remaining = max(0, query_limit() - len(_active(db, bid)))
                for domain, family in owned.items():
                    if domain in known:
                        continue
                    seeds = [q for q in candidates if bid in q.get('business_ids', []) and
                             set(q.get('product_ids', [])) & set(family.member_product_ids)][:6]
                    for query in seeds:
                        if remaining and _insert(db, bid, family, query['query'], query.get('kind', 'intent'), now):
                            remaining -= 1
                rows = [r for r in _active(db, bid) if r['domain'] in owned]
                product_ids = [pid for f in owned.values() for pid in f.member_product_ids]
                metrics = read_metrics(db, config, product_ids, now)
                required = {q['query'] for q in candidates if q.get('required') and bid in q.get('business_ids', [])}
                if enabled_businesses is None or bid in enabled_businesses:
                    _evaluate(db, config, bid, owned, rows, metrics, generator, now, required)
                rows = [r for r in _active(db, bid) if r['domain'] in owned][:query_limit()]
                scored = [{**r, **metrics['queries'].get(r['query'], {})} for r in rows]
                rates = weekly_scores(scored)
                for row in rows:
                    family = owned[row['domain']]
                    evidence = metrics['queries'].get(row['query'], {})
                    runs, leads = evidence.get('runs', 0), evidence.get('leads', 0)
                    db.execute('UPDATE public.discovery_xsearchquery SET weight=%s WHERE id=%s AND weight IS DISTINCT FROM %s',
                               (rates[row['query']], row['id'], rates[row['query']]))
                    if enabled_businesses is not None and bid not in enabled_businesses:
                        continue
                    item = merged.setdefault(row['query'], {'query': row['query'], 'kind': 'intent', 'priority': 1,
                        'business_ids': [], 'product_ids': [], 'product_business_ids': {}, 'portfolio_managed': True,
                        'tenant_scores': {}, 'weekly_stats': {'searches': 0, 'leads': 0, 'lead_rate': 0, 'starting_score': 0}})
                    item['business_ids'].append(bid)
                    item['product_ids'].extend(family.member_product_ids)
                    item['product_business_ids'].update({pid: bid for pid in family.member_product_ids})
                    item['tenant_scores'][bid] = rates[row['query']]
                    item['weekly_stats']['searches'] += runs
                    item['weekly_stats']['leads'] += leads
                    total = item['weekly_stats']['searches']
                    item['weekly_stats']['lead_rate'] = item['weekly_stats']['leads'] / total if total else 0
                    item['weekly_stats']['starting_score'] = sum(item['tenant_scores'].values()) / len(item['tenant_scores'])
    return list(merged.values())


def _generate(config, family, winners, existing):
    from need_engine.llm import LLMClient
    from need_engine.store import Store
    from .proqe import expand_queries_with_proqe
    import os
    store = Store(config.database_url, config.state_schema)
    llm = None
    try:
        llm = LLMClient(config, store)
        return expand_queries_with_proqe(family, os.getenv('X_COLLECT_STATE_FILE', 'output/x_collector_state.sqlite3'),
            llm=llm, model=config.extract_model, existing_queries=existing,
            weekly_context={'tenant_winners': winners, 'product_type': family.product_type,
                            'category': family.category_path, 'word_counts': [positive_word_count(r['query']) for r in winners]})
    finally:
        if llm is not None:
            llm.registry.close()
        store.close()
