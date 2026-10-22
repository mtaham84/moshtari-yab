from instagram_discovery.analyzer import StaticCategoryKnowledge, analyze_post, knowledge_from_seeds


def knowledge(*categories):
    return StaticCategoryKnowledge(*categories)


def post(caption, hashtags):
    return {
        "id": "Media123", "shortcode": "Media123", "username": "shop_page",
        "caption": caption, "hashtags": hashtags,
        "url": "https://www.instagram.com/p/Media123/",
    }


def test_confident_category_match_uses_caption_post_hashtag_and_product_terms():
    target = knowledge({
        "id": 12, "business_id": 7, "name": "کفش", "full_path": "پوشاک > کفش",
        "keywords": ["کفش چرمی", "سایز"], "hashtags": ["کفش_چرمی"],
        "products": [{"id": 19, "name": "کفش چرمی مردانه"}],
    })

    result = analyze_post(
        post("برای خرید کفش چرمی مردانه این مدل موجود است؛ برای انتخاب سایز پیام بدهید.", ["کفش_چرمی"]),
        {"kind": "hashtag", "value": "کفش", "business_id": 7, "category_id": 12}, target,
    )["category_decision"]

    assert result["result"] == "confident_match"
    assert result["category_id"] == 12
    assert result["product_id"] == 19
    assert result["knowledge_source"]["category_id"] == 12
    assert result["score"] >= 0.75
    assert result["reason"]["evidence"]["caption_terms"]
    assert result["reason"]["evidence"]["post_hashtags"] == ["کفش_چرمی"]
    assert result["knowledge_source"]["business_id"] == 7
    assert "کفش" not in result["reason"]["evidence"]["post_hashtags"]


def test_short_caption_goes_to_review_without_a_confident_category():
    target = knowledge({
        "id": 12, "business_id": 7, "name": "کفش", "keywords": ["کفش چرمی"],
        "hashtags": ["کفش_چرمی"], "products": [{"id": 19, "name": "کفش چرمی"}],
    })

    result = analyze_post(post("کفش چرمی", ["کفش_چرمی"]), {"kind": "hashtag", "value": "کفش"}, target)["category_decision"]

    assert result["result"] == "human_review"
    assert result["category_id"] is None
    assert result["category_candidate_id"] == 12
    assert result["score"] == 1.0
    assert result["reason"]["code"] == "caption_too_short"


def test_equally_scored_categories_are_ambiguous_and_go_to_review():
    target = knowledge(
        {"id": 12, "name": "کفش", "keywords": ["چرم"], "hashtags": ["چرم"]},
        {"id": 13, "name": "کیف", "keywords": ["چرم"], "hashtags": ["چرم"]},
    )

    result = analyze_post(
        post("برای خرید چرم طبیعی با کیفیت بالا این مدل جدید را ببینید و سفارش دهید.", ["چرم"]),
        {"kind": "hashtag", "value": "چرم"}, target,
    )["category_decision"]

    assert result["result"] == "human_review"
    assert result["reason"]["code"] == "ambiguous_category"
    assert result["category_id"] is None


def test_unmatched_post_is_rejected_without_using_source_hashtag_as_evidence():
    target = knowledge({
        "id": 12, "business_id": 7, "name": "کفش", "keywords": ["کفش چرمی"],
        "hashtags": ["کفش"], "products": [{"id": 19, "name": "کفش چرمی مردانه"}],
    })

    result = analyze_post(
        post("این هفته از سفر برگشتم و چند عکس از مسیرهای زیبای شمال گذاشتم تا خاطره‌ها ثبت شوند.", []),
        {"kind": "hashtag", "value": "کفش"}, target,
    )["category_decision"]

    assert result["result"] == "rejected"
    assert result["category_id"] is None
    assert result["knowledge_source"]["kind"] == "seller_category_seed"


def test_seed_knowledge_combines_products_and_hashtags_for_one_category():
    seeds = [
        {"kind": "hashtag", "value": "کفش", "business_id": 7, "category_id": 12,
         "category": "کفش", "terms": ["کفش چرمی"], "product_ids": [19],
         "products": ["کفش چرمی مردانه"]},
        {"kind": "hashtag", "value": "بوت", "business_id": 7, "category_id": 12,
         "category": "کفش", "terms": ["سایز"], "product_ids": [20], "products": ["بوت زنانه"]},
    ]

    categories = knowledge_from_seeds(seeds, business_id=7).categories_for({"business_id": 7})

    assert len(categories) == 1
    assert categories[0]["keywords"] == ["کفش چرمی", "سایز"]
    assert categories[0]["hashtags"] == ["کفش", "بوت"]
    assert [product["id"] for product in categories[0]["products"]] == [19, 20]
