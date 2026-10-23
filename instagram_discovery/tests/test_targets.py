from instagram_discovery.targets import HashtagTarget, build_hashtag_seeds


def test_hashtag_seeds_keep_category_products_terms_caps_and_windows():
    target = HashtagTarget(
        business_id=3, category_id=12, category_name="کفش", category_path="پوشاک > کفش",
        product_ids=(8, 9), product_names=("کفش چرم", "بوت"),
        terms=("چرم", "سایز"), hashtags=("#کفش", "کفش"), max_posts=6, window_days=14,
    )

    seeds = build_hashtag_seeds([target])

    assert seeds == [{
        "tag": "کفش", "limit": 6, "window_days": 14, "business_id": 3,
        "category_id": 12, "category": "کفش", "category_path": "پوشاک > کفش",
        "product_ids": [8, 9], "products": ["کفش چرم", "بوت"],
        "terms": ["چرم", "سایز"],
    }]


def test_hashtag_seeds_remain_separate_for_different_business_categories():
    targets = [
        HashtagTarget(2, 10, "کفش", "پوشاک > کفش", (), (), ("چرم",), ("#کفش",), 8, 14),
        HashtagTarget(3, 10, "کفش", "پوشاک > کفش", (), (), ("سایز",), ("کفش",), 5, 7),
    ]

    seeds = build_hashtag_seeds(targets)

    assert [(seed["business_id"], seed["category_id"], seed["tag"], seed["terms"]) for seed in seeds] == [
        (2, 10, "کفش", ["چرم"]),
        (3, 10, "کفش", ["سایز"]),
    ]


def test_category_and_product_targets_merge_for_the_same_hashtag():
    targets = [
        HashtagTarget(2, 10, "کفش", "پوشاک > کفش", (8,), ("کفش چرمی",), ("چرم",), ("#کفش",), 8, 14),
        HashtagTarget(2, 10, "کفش", "پوشاک > کفش", (9,), ("بوت",), ("سایز",), ("کفش",), 5, 7),
    ]

    seeds = build_hashtag_seeds(targets)

    assert len(seeds) == 1
    assert seeds[0]["product_ids"] == [8, 9]
    assert seeds[0]["products"] == ["کفش چرمی", "بوت"]
    assert seeds[0]["terms"] == ["چرم", "سایز"]
    assert (seeds[0]["limit"], seeds[0]["window_days"]) == (5, 7)
