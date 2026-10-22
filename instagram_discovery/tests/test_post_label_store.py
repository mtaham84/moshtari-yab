from __future__ import annotations

import json

from instagram_discovery.post_label_store import FilePostLabelStore, PostLabelStore


def test_file_post_label_store_implements_protocol_for_load_save_and_pending(tmp_path):
    store = FilePostLabelStore(tmp_path)
    assert isinstance(store, PostLabelStore)
    pending_post = {
        "id": "ig:Abcde12",
        "url": "https://www.instagram.com/p/Abcde12/",
        "text": "کپشن کوتاه",
        "created_at": "2026-10-23T10:00:00+00:00",
        "metadata": {
            "collection_source": {"kind": "hashtag", "business_id": 7},
            "category_decision": {
                "result": "human_review", "score": 0.4,
                "category_candidate_id": None, "product_candidate_id": None,
                "reason": {"code": "caption_too_short", "evidence": {"caption_excerpt": "کپشن کوتاه"}},
            },
        },
    }
    store.queue_path.write_text(json.dumps(pending_post, ensure_ascii=False) + "\n", encoding="utf-8")

    page, total = store.pending(business_id=7, limit=10)
    assert total == 1
    assert page[0]["post_id"] == "ig:Abcde12"

    store.save(
        post_id="ig:Abcde12", business_id=7, decision="approve", category_id=3,
        product_id=None, operator_id=19,
        snapshot={
            "result": "human_review", "score": 0.4, "reason_code": "caption_too_short",
            "candidate_category_id": None, "candidate_product_id": None,
        },
    )

    labels = store.load(business_id=7)
    assert labels[("7", "ig:Abcde12")]["category_id"] == "3"
    assert store.pending(business_id=7, limit=10)[1] == 0

