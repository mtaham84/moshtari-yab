from types import SimpleNamespace

from workers.x_collector.families import build_families, family_id, path_head
from workers.x_collector.query_source import targeted_queries_from_families, targeted_queries_from_products


def prod(pid, biz, title, path, x=True, prio=1):
    return SimpleNamespace(product_id=str(pid), business_id=str(biz), title=title, category_path=path, category_keywords=[],
                           product_type="", discovery_priority=prio, x_search_enabled=x, description="")


def test_products_group_by_seller_and_first_three_levels():
    products = [prod(1, 7, "پیراهن آبی", "پوشاک > مردانه > پیراهن"), prod(2, 7, "پیراهن سفید", "پوشاک > مردانه > پیراهن"),
                prod(3, 7, "شومیز", "پوشاک > زنانه > شومیز"), prod(4, 8, "پیراهن", "پوشاک > مردانه > پیراهن"),
                prod(5, 7, "پیراهن یقه دار", "پوشاک > مردانه > پیراهن > یقه دار")]     # level 4 collapses into level 3
    fams = {f.product_id: f for f in build_families(products)}
    assert len(fams) == 3
    shirts = fams[family_id(7, "پوشاک > مردانه > پیراهن")]
    assert shirts.member_product_ids == ["1", "2", "5"] and shirts.title == "پیراهن"
    assert path_head("الف > ب > ج > د") == "الف > ب > ج"


def test_notes_reach_the_family_description_and_hash():
    products = [prod(1, 7, "جوراب", "پوشاک > جوراب")]
    a = build_families(products, {"7": "عمده فروشی به مغازه دارها"})[0]
    b = build_families(products, {})[0]
    assert "عمده فروشی" in a.description and "SELLER NOTES" in a.description
    assert a.content_hash() != b.content_hash()


def test_queries_are_tagged_with_every_member_and_capped_per_seller(monkeypatch):
    monkeypatch.setenv("X_QUERY_MAX_PER_TENANT", "5")
    fams = build_families([prod(1, 7, "پیراهن", "پوشاک > مردانه > پیراهن"), prod(2, 7, "تیشرت", "پوشاک > مردانه > تیشرت"),
                           prod(3, 7, "شومیز", "پوشاک > زنانه > شومیز")])
    terms = {f.product_id: {"names": [f.title + " مردانه"], "pain_symptoms": [f.title + " تنگ", f.title + " گشاد"],
                             "advice_requests": ["پیشنهاد " + f.title], "implicit_solutions": ["خرید " + f.title]} for f in fams}
    out = targeted_queries_from_families(fams, terms=terms)
    assert len(out) <= 5
    assert {pid for q in out for pid in q["product_ids"]} >= {"1", "2", "3"}      # every family got a slot (round robin)
    assert len({q["query"].casefold() for q in out}) == len(out)                   # no duplicates


def test_identical_queries_of_two_sellers_are_one_search():
    fams = build_families([prod(1, 7, "جوراب", "پوشاک > جوراب"), prod(2, 8, "جوراب", "پوشاک > جوراب")])
    terms = {f.product_id: {"names": ["جوراب مردانه"], "pain_symptoms": [], "advice_requests": [], "implicit_solutions": []} for f in fams}
    out = targeted_queries_from_families(fams, terms=terms)
    hit = next(q for q in out if q["query"].startswith('"جوراب مردانه"'))
    assert hit["business_ids"] == ["7", "8"] and hit["product_ids"] == ["1", "2"]


def test_only_x_enabled_products_are_searched():
    out = targeted_queries_from_products([prod(1, 7, "جوراب", "پوشاک > جوراب", x=False)], terms={})
    assert out == []
