"""Business types → category trees → product families.

* A fixed list of top-level ``BusinessType`` rows (پوشاک، خوراکی، آموزش …).
* The first time ANY seller picks a type, the AI builds that type's sub-category tree (levels 2-3) once; the tree is
  stored as global ``Category`` rows (business=None) and reused for every later seller (no second model call).
* A seller adopts nodes of that tree into his OWN editable copy (``Category.business`` = seller, ``origin`` = system node).
  He may add/rename/delete nodes and add one extra level (max depth 4). Search always collapses to depth <= 3.
* Every 5 new products the AI places un-categorised products into the seller's tree (creating nodes when needed).
* A *family* is the (collapsed) category of a product; queries are generated per family.
"""
from __future__ import annotations

import logging
import re
from datetime import timedelta
from typing import Callable

from django.db import transaction
from django.utils import timezone

from apps.businesses.models import Business, BusinessType
from apps.products.models import Category, Product

log = logging.getLogger("products.taxonomy")

SEARCH_DEPTH = 3      # queries are built from the first three levels only
MAX_DEPTH = 4         # a seller may add one level below the system's three
AI_MAX_DEPTH = 3
ORGANIZE_EVERY = 5
BUILD_STALE_AFTER = timedelta(minutes=4)


class TaxonomyError(Exception):
    pass


def _clean_name(value) -> str:
    text = re.sub(r"[\"“”«»<>\n\r\t]", " ", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()[:60]


def _norm(value: str) -> str:
    from need_engine.text import norm
    return norm(value)


# ------------------------------------------------------------------------------------------------ system trees ------
TREE_SYSTEM = """You design product/service category trees for an Iranian online marketplace (Persian UI).
Given ONE top-level business type, return a practical category tree below it that real Iranian sellers and service
providers would use to organise what they sell.

RULES
- Level 1 is the business type itself (do not repeat it). You return level 2 and level 3 only.
- Level 2 splits the type in the most natural way for that business (audience: مردانه/زنانه/بچگانه; family of goods;
  kind of service). 4 to 9 nodes.
- Level 3 are concrete item or service types people search for (پیراهن، تیشرت، جوراب، ایمپلنت، آموزش پایتون ...).
  3 to 8 per level-2 node. Only add level 3 when it is genuinely useful; otherwise leave "children" empty.
- NEVER add a level for the sake of it. No filler, no duplicates, no overlap between siblings, no brands, no colours,
  no adjectives such as «بهترین» or «ارزان».
- Names are Persian, 1 to 3 words, singular/plural as sellers write them, no punctuation.
- Prefer coverage of what is commonly sold in Iran over exotic items.

Return strictly JSON: {"children":[{"name":"...","children":[{"name":"...","children":[]}]}]}"""


def _root_for(btype: BusinessType) -> Category:
    if btype.root_category_id:
        return btype.root_category
    root = Category.objects.filter(business__isnull=True, parent__isnull=True, name=btype.name).first()
    if not root:
        root = Category.objects.create(name=btype.name, product_type=btype.kind, source="system",
                                       description=btype.description or "")
    btype.root_category = root
    btype.save(update_fields=["root_category"])
    return root


def _store_children(parent: Category, nodes, depth: int, kind: str) -> int:
    created = 0
    seen: set[str] = set()
    limit = {2: 10, 3: 10}.get(depth, 0)
    for node in (nodes or [])[:limit]:
        if not isinstance(node, dict):
            continue
        name = _clean_name(node.get("name"))
        key = _norm(name)
        if not name or key in seen or len(name.split()) > 4:
            continue
        seen.add(key)
        cat = Category.objects.create(name=name, parent=parent, product_type=kind, source="ai")
        created += 1
        if depth < AI_MAX_DEPTH:
            created += _store_children(cat, node.get("children"), depth + 1, kind)
    return created


def ensure_type_tree(btype: BusinessType, llm: Callable | None = None) -> BusinessType:
    """Builds the tree with the AI exactly once per type (cached forever in the DB)."""
    if llm is None:
        from apps.core.llm import chat_json as llm
    with transaction.atomic():
        locked = BusinessType.objects.select_for_update().get(pk=btype.pk)
        if locked.tree_status == BusinessType.TREE_READY and locked.root_category_id and locked.root_category.children.exists():
            return locked
        if (locked.tree_status == BusinessType.TREE_BUILDING and locked.tree_built_at
                and timezone.now() - locked.tree_built_at < BUILD_STALE_AFTER):
            raise TaxonomyError("درخت این دسته در حال ساخت است؛ چند لحظه بعد دوباره تلاش کنید.")
        locked.tree_status = BusinessType.TREE_BUILDING
        locked.tree_built_at = timezone.now()
        locked.save(update_fields=["tree_status", "tree_built_at"])
    try:
        data = llm(TREE_SYSTEM, f"Business type: {locked.name} ({locked.description}). Kind: "
                   f"{'services' if locked.kind == 'SERVICE' else 'physical goods'}.", purpose="taxonomy_tree")
        nodes = data.get("children") if isinstance(data, dict) else None
        if not nodes:
            raise TaxonomyError("پاسخ هوش مصنوعی ساختار معتبری نداشت.")
        with transaction.atomic():
            root = _root_for(locked)
            root.children.filter(source="ai").delete()      # rebuild after a failed/partial attempt
            made = _store_children(root, nodes, 2, locked.kind)
            if not made:
                raise TaxonomyError("هیچ زیردسته‌ای ساخته نشد.")
            locked.tree_status, locked.tree_error = BusinessType.TREE_READY, ""
            locked.tree_built_at = timezone.now()
            locked.save(update_fields=["tree_status", "tree_error", "tree_built_at"])
        log.info("taxonomy tree for %s built: %d nodes", locked.key, made)
        return locked
    except Exception as exc:
        BusinessType.objects.filter(pk=locked.pk).update(tree_status=BusinessType.TREE_FAILED, tree_error=str(exc)[:250])
        if isinstance(exc, TaxonomyError):
            raise
        raise TaxonomyError(f"ساخت درخت دسته‌بندی ناموفق بود: {exc}") from exc


def _node_dict(cat: Category, children_map: dict, extra=None) -> dict:
    d = {"id": cat.id, "name": cat.name, "depth": cat.depth, "children": [_node_dict(c, children_map, extra) for c in children_map.get(cat.id, [])]}
    if extra:
        d.update(extra(cat))
    return d


def system_tree(btype: BusinessType) -> list[dict]:
    """Nested nodes under the type's root (level 2 and below)."""
    if not btype.root_category_id:
        return []
    nodes = list(Category.objects.filter(business__isnull=True, is_active=True, full_path__startswith=btype.root_category.full_path + " > ")
                 .order_by("depth", "id"))
    cmap: dict[int, list[Category]] = {}
    for c in nodes:
        cmap.setdefault(c.parent_id, []).append(c)
    return [_node_dict(c, cmap) for c in cmap.get(btype.root_category_id, [])]


# --------------------------------------------------------------------------------------------- seller's own tree ------
def _own(business: Business):
    return Category.objects.filter(business=business, is_active=True)


def adopt_nodes(business: Business, node_ids: list[int], btype: BusinessType | None = None) -> int:
    """Copies the chosen system nodes (and their ancestors) into the seller's own tree. Idempotent."""
    wanted = list(Category.objects.filter(pk__in=node_ids, business__isnull=True).select_related("parent"))
    created = 0
    with transaction.atomic():
        copies = {c.origin_id: c for c in _own(business) if c.origin_id}
        def ensure(node: Category) -> Category:
            nonlocal created
            if node.pk in copies:
                return copies[node.pk]
            parent_copy = ensure(node.parent) if node.parent_id and node.parent.business_id is None else None
            cp = Category.objects.create(business=business, name=node.name, parent=parent_copy, product_type=node.product_type,
                                         origin=node, source="system")
            copies[node.pk] = cp
            created += 1
            return cp
        for node in wanted:
            ensure(node)
    return created


def own_tree(business: Business) -> list[dict]:
    cats = list(_own(business).order_by("depth", "id"))
    ids = {c.id for c in cats}
    cmap: dict[int | None, list[Category]] = {}
    for c in cats:
        cmap.setdefault(c.parent_id if c.parent_id in ids else None, []).append(c)
    counts = {}
    for row in Product.objects.filter(business=business, category__isnull=False).values_list("category_id", flat=True):
        counts[row] = counts.get(row, 0) + 1

    def extra(c: Category) -> dict:
        return {"products": counts.get(c.id, 0), "source": c.source}
    return [_node_dict(c, cmap, extra) for c in cmap.get(None, [])]


def _unique_sibling(business: Business, parent: Category | None, name: str, exclude_id: int | None = None) -> bool:
    qs = _own(business).filter(parent=parent, normalized_name=_norm(name))
    if exclude_id:
        qs = qs.exclude(pk=exclude_id)
    return not qs.exists()


def add_node(business: Business, parent_id: int | None, name: str, source: str = "user") -> Category:
    name = _clean_name(name)
    if not name:
        raise TaxonomyError("نام دسته‌بندی الزامی است.")
    parent = _own(business).filter(pk=parent_id).first() if parent_id else None
    if parent_id and not parent:
        system = Category.objects.filter(pk=parent_id, business__isnull=True).first()   # older sellers use the shared tree
        if system:
            adopt_nodes(business, [system.pk])
            parent = _own(business).filter(origin=system).first()
    if parent_id and not parent:
        raise TaxonomyError("دستهٔ والد پیدا نشد.")
    depth = (parent.depth + 1) if parent else 1
    if depth > MAX_DEPTH:
        raise TaxonomyError("حداکثر عمق دسته‌بندی ۴ سطح است.")
    if source == "ai" and depth > AI_MAX_DEPTH:
        raise TaxonomyError("depth")
    if not _unique_sibling(business, parent, name):
        raise TaxonomyError("این دسته‌بندی قبلاً در همین سطح وجود دارد.")
    kind = parent.product_type if parent else ("SERVICE" if business.business_type == "SERVICE" else "PHYSICAL")
    return Category.objects.create(business=business, name=name, parent=parent, product_type=kind, source=source)


def rename_node(business: Business, node_id: int, name: str) -> Category:
    cat = _own(business).filter(pk=node_id).first()
    name = _clean_name(name)
    if not cat or not name:
        raise TaxonomyError("دسته‌بندی پیدا نشد یا نام خالی است.")
    if not _unique_sibling(business, cat.parent, name, exclude_id=cat.pk):
        raise TaxonomyError("این نام قبلاً در همین سطح وجود دارد.")
    cat.name = name
    cat.save()
    for child in list(cat.children.all()):    # keep every descendant's full_path in sync
        _refresh_paths(child)
    return cat


def _refresh_paths(cat: Category) -> None:
    cat.save()
    for child in cat.children.all():
        _refresh_paths(child)


def delete_node(business: Business, node_id: int) -> int:
    """Deletes a node with its sub-tree; its products move to the parent (or lose the category if it was a root)."""
    cat = _own(business).filter(pk=node_id).first()
    if not cat:
        raise TaxonomyError("دسته‌بندی پیدا نشد.")
    with transaction.atomic():
        ids, stack = [], [cat]
        while stack:
            c = stack.pop()
            ids.append(c.pk)
            stack.extend(c.children.all())
        Product.objects.filter(business=business, category_id__in=ids).update(category=cat.parent_id)
        Category.objects.filter(pk__in=ids).delete()
    return len(ids)


# ------------------------------------------------------------------------------------------------- families ---------
def collapse(cat: Category | None) -> Category | None:
    """The ancestor (or the node itself) at depth <= SEARCH_DEPTH."""
    while cat is not None and cat.depth > SEARCH_DEPTH:
        cat = cat.parent
    return cat


def family_key(business_id: int, category_path: str | None) -> str:
    """Stable id of a family: seller + first three levels of the category path."""
    import hashlib
    parts = [p.strip() for p in str(category_path or "").split(">") if p.strip()][:SEARCH_DEPTH]
    return f"f{business_id}-{hashlib.md5(' > '.join(parts).encode()).hexdigest()[:8]}"


def families_for(business: Business) -> dict[int | None, list[Product]]:
    out: dict[int | None, list[Product]] = {}
    for p in Product.objects.filter(business=business, status="ACTIVE").select_related("category", "category__parent"):
        fam = collapse(p.category)
        out.setdefault(fam.pk if fam else None, []).append(p)
    return out


# ------------------------------------------------------------------------------------------ AI product organiser ------
ORGANIZE_SYSTEM = """You organise the products/services of ONE Iranian seller into his category tree (Persian).
Input: the seller's tree (each node has an id and a path) and his products that still have no proper category.
For every product choose the BEST existing node id. Prefer the deepest fitting node. If no node fits, propose ONE new
node: {"parent_id": <existing node id or null for a new top node>, "name": "<1-3 Persian words>"}.
RULES: never create more than 3 levels; do not create a node when an existing one fits; names are generic type names
(پیراهن مردانه, جوراب) — no brands, colours, sizes or adjectives; reuse one new node for several similar products.
Return strictly JSON: {"assignments":[{"product_id":1,"category_id":12,"new_category":null},
{"product_id":2,"category_id":null,"new_category":{"parent_id":12,"name":"شومیز زنانه"}}]}"""


def organize_products(business: Business, llm: Callable | None = None) -> dict:
    """Places products that have no tree category into the seller's tree (creating nodes if needed)."""
    if llm is None:
        from apps.core.llm import chat_json as llm
    products = list(Product.objects.filter(business=business, status="ACTIVE"))
    own_ids = set(_own(business).values_list("id", flat=True))
    todo = [p for p in products if p.category_id not in own_ids]
    stats = {"checked": len(products), "placed": 0, "created": 0}
    if not todo:
        Business.objects.filter(pk=business.pk).update(organized_product_count=len(products))
        return stats
    tree_lines = [f"{c.id}: {c.full_path}" for c in _own(business).order_by("full_path")] or ["(empty tree)"]
    notes = (business.search_notes or "")[:400]
    user = ("Seller: %s | domain: %s | types: %s\nNotes: %s\n\nTREE:\n%s\n\nPRODUCTS:\n%s" % (
        business.name, business.business_domain, ", ".join(business.business_types.values_list("name", flat=True)), notes,
        "\n".join(tree_lines), "\n".join(f"{p.id}: {p.name} — {(p.description or '')[:120]}" for p in todo[:40])))
    data = llm(ORGANIZE_SYSTEM, user, purpose="organize_products", business_id=business.pk)
    by_id = {p.id: p for p in todo}
    new_nodes: dict[tuple, Category] = {}
    for item in (data or {}).get("assignments", []) if isinstance(data, dict) else []:
        p = by_id.get(item.get("product_id"))
        if not p:
            continue
        cat = _own(business).filter(pk=item.get("category_id")).first() if item.get("category_id") else None
        spec = item.get("new_category")
        if not cat and isinstance(spec, dict) and spec.get("name"):
            parent_id = spec.get("parent_id") if spec.get("parent_id") in own_ids or spec.get("parent_id") in {n.pk for n in new_nodes.values()} else None
            key = (parent_id, _norm(_clean_name(spec["name"])))
            if key not in new_nodes:
                try:
                    existing = _own(business).filter(parent_id=parent_id, normalized_name=key[1]).first()
                    new_nodes[key] = existing or add_node(business, parent_id, spec["name"], source="ai")
                    stats["created"] += 0 if existing else 1
                except TaxonomyError:
                    continue
            cat = new_nodes[key]
        if cat:
            Product.objects.filter(pk=p.pk).update(category=cat)
            stats["placed"] += 1
    Business.objects.filter(pk=business.pk).update(organized_product_count=len(products))
    return stats


def maybe_organize(business: Business, background: bool = True) -> bool:
    """Called after a product is added: every 5 new products the AI reviews the catalogue."""
    count = Product.objects.filter(business=business, status="ACTIVE").count()
    business.refresh_from_db(fields=["organized_product_count"])
    if count < ORGANIZE_EVERY or count - business.organized_product_count < ORGANIZE_EVERY:
        return False

    def run():
        try:
            from django.db import connection
            organize_products(Business.objects.get(pk=business.pk))
        except Exception:
            log.warning("organize_products failed for business %s", business.pk, exc_info=True)
        finally:
            from django.db import connection
            connection.close()
    if background:
        import threading
        threading.Thread(target=run, daemon=True).start()
    else:
        run()
    return True
