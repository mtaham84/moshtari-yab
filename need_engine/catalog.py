"""Product side: one LLM card per product (only when new or changed), vectors, and an in-memory search index."""
from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from need_engine.config import EngineConfig
from need_engine.embeddings import Embedder
from need_engine.llm import LLMClient
from need_engine.prompts import PRODUCT_CARD_SYSTEM
from need_engine.schemas import Product, ProductCard
from need_engine.store import Store
from need_engine.text import BM25

log = logging.getLogger("need_engine.catalog")


def _listing(p: Product) -> dict:
    a = "، ".join(f"{k}: {v}" for k, v in list(p.attributes.items())[:8])
    return {"product_id": p.product_id, "title": p.title, "product_type": p.product_type, "category": p.category_path,
            "category_keywords": p.category_keywords, "description": p.description[:600], "price_toman": p.price_toman,
            "city": p.city, "attributes": a, "tags": p.tags or None}


def _vec_texts(p: Product, c: ProductCard) -> tuple[list[str], list[str]]:
    what = " | ".join(x for x in [p.title, p.product_type, p.category_path, "، ".join(p.category_keywords), "، ".join(c.aliases), c.what_it_is] if x)
    texts, kinds = [what], ["what"]
    for prob in c.problems_solved[:4]:
        texts.append(prob)
        kinds.append("problem")
    return texts, kinds


def card_from_dict(pid: str, c: dict) -> ProductCard:
    def opt(v):
        v = "" if v is None else str(v).strip()
        return None if v in ("", "null") else v

    return ProductCard(product_id=pid, what_it_is=str(c.get("what_it_is") or ""),
                       aliases=[str(x) for x in c.get("aliases") or [] if str(x).strip()][:5],
                       problems_solved=[str(x) for x in c.get("problems_solved") or [] if str(x).strip()][:4],
                       audience=opt(c.get("audience")), use=opt(c.get("use")), level=opt(c.get("level")))


class Catalog:
    def __init__(self, cfg: EngineConfig, store: Store, llm: LLMClient, emb: Embedder):
        self.cfg, self.store, self.llm, self.emb = cfg, store, llm, emb
        self.products: list[Product] = []
        self.cards: dict[str, ProductCard] = {}
        self.pid_index: dict[str, int] = {}
        self.V = np.zeros((0, 1), dtype=np.float32)
        self.owner = np.zeros(0, dtype=int)
        self.bm25: BM25 | None = None
        self.held: dict[str, int] = {}    # seller → new/changed products waiting for payment

    @property
    def n(self) -> int:
        return len(self.products)

    def _cards_for(self, prods: list[Product]) -> dict[str, ProductCard]:
        def run(batch: list[Product]) -> list[dict]:
            user = json.dumps({"listings": [_listing(p) for p in batch]}, ensure_ascii=False)
            data, _ = self.llm.complete_json("product_cards", self.cfg.extract_model, PRODUCT_CARD_SYSTEM, user, max_tokens=4096,
                                             ref=",".join(p.product_id for p in batch),
                                             businesses=[p.business_id for p in batch])
            return [c for c in (data or {}).get("cards", []) if isinstance(c, dict)]

        got: dict[str, ProductCard] = {}
        for p in prods:   # the seller edited the card → used as is, no LLM call
            if p.card_override:
                got[p.product_id] = card_from_dict(p.product_id, p.card_override)
        todo = [p for p in prods if p.product_id not in got]
        for size in (10, 3, 1):  # products the model skipped are retried in smaller batches
            if not todo:
                break
            batches = [todo[i:i + size] for i in range(0, len(todo), size)]
            with ThreadPoolExecutor(max_workers=self.cfg.max_workers) as ex:
                for cards in ex.map(run, batches):
                    for c in cards:
                        pid = str(c.get("product_id"))
                        try:
                            got[pid] = card_from_dict(pid, c)
                        except Exception:
                            continue
            todo = [p for p in todo if p.product_id not in got]
        for p in todo:
            log.warning("product %s got no card; title/type are used instead", p.product_id)
            got[p.product_id] = ProductCard(product_id=p.product_id)
        return got

    def sync(self, products: list[Product], hold: frozenset[str] = frozenset()) -> list[str]:
        """Builds cards/vectors only for new or changed products. Returns ids of new or changed products.

        ``hold`` = sellers whose balance is used up: their new/changed products are not carded (that LLM call is theirs)
        and wait — the stored version, if any, stays — until they top up; then they show up here as changed."""
        known = self.store.product_hashes()
        current = {p.product_id: p for p in products}
        removed = [pid for pid in known if pid not in current]
        if removed:
            self.store.delete_products(removed)
        changed = [p for p in products if known.get(p.product_id) != p.content_hash()]
        self.held = {}
        for p in changed:
            if p.business_id is not None and str(p.business_id) in hold:
                self.held[str(p.business_id)] = self.held.get(str(p.business_id), 0) + 1
        changed = [p for p in changed if p.business_id is None or str(p.business_id) not in hold]
        if changed:
            log.info("product cards: %d new/changed of %d", len(changed), len(products))
            cards = self._cards_for(changed)
            all_texts, spans = [], []
            for p in changed:
                t, k = _vec_texts(p, cards[p.product_id])
                spans.append((p, cards[p.product_id], len(all_texts), len(t), k))
                all_texts += t
            V = self.emb.encode(all_texts, stage="embed_products")
            for p, c, s, ln, k in spans:
                self.store.save_product(p, c, V[s:s + ln], k)
        self._load()
        for p in self.products:   # url is not in the hash: always take the current one
            if p.product_id in current:
                p.url = current[p.product_id].url
                p.x_outreach_enabled = current[p.product_id].x_outreach_enabled
        return [p.product_id for p in changed]

    def _load(self) -> None:
        rows = self.store.products()
        self.products = [p for p, _, _ in rows]
        self.cards = {p.product_id: c for p, c, _ in rows}
        self.pid_index = {p.product_id: i for i, p in enumerate(self.products)}
        vecs = [(i, v) for i, (_, _, vs) in enumerate(rows) for v in (vs if vs is not None else [])]
        self.V = np.stack([v for _, v in vecs]).astype(np.float32) if vecs else np.zeros((0, 1), dtype=np.float32)
        self.owner = np.array([i for i, _ in vecs], dtype=int)
        docs = []
        for p, c, _ in rows:
            docs.append(" ".join(str(x) for x in [p.title, p.product_type, p.description, " ".join(p.tags),
                                                   p.category_path, " ".join(p.category_keywords),
                                                   " ".join(c.aliases), " ".join(c.problems_solved)] if x))
        self.bm25 = BM25(docs) if docs else None

    def dense_scores(self, qvecs: np.ndarray) -> np.ndarray:
        """max similarity over (need query vectors × product vectors), per product."""
        out = np.full(self.n, -1.0, dtype=np.float32)
        if len(qvecs) == 0 or len(self.V) == 0 or qvecs.shape[1] != self.V.shape[1]:
            return out
        s = (qvecs @ self.V.T).max(axis=0)
        np.maximum.at(out, self.owner, s)
        return out

    def product_vectors(self, pid: str) -> np.ndarray:
        return self.V[self.owner == self.pid_index[pid]]

    def line(self, j: int) -> str:
        p = self.products[j]
        price = f"{int(p.price_toman):,} تومان" if p.price_toman else "قیمت نامشخص"
        city = p.city or "آنلاین"
        ship = "ارسال سراسری" if p.ships_nationwide else "فقط محلی"
        return f"[{p.product_id}] {p.title} | {p.product_type} | {price} | {city} ({ship}) | {p.description[:140]}"
