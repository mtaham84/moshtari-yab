from __future__ import annotations

from instagram_discovery.comment_evaluation import (
    KnowledgeSnapshot,
    evaluate_with_jev,
    prefilter_comment,
)


class StubKnowledgeReader:
    def __init__(self):
        self.calls = []
        self.snapshot = KnowledgeSnapshot(
            related_category={"id": 7, "name": "کفش"},
            related_product={"id": 9, "name": "کفش چرمی"},
            answers=[{"question": "ارسال", "answer": "ارسال سراسری"}],
            indexed_information=[{"document_id": "doc-1", "text": "کفش چرم طبیعی"}],
            source={"kind": "mock_index", "document_ids": ["doc-1"]},
        )

    def retrieve_for_comment(self, **kwargs):
        self.calls.append(kwargs)
        return self.snapshot


class StubJev:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def evaluate_comment(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def test_evaluation_retrieves_knowledge_and_stores_explainable_jev_decision():
    reader = StubKnowledgeReader()
    jev = StubJev({
        "status": "probably a buyer",
        "related": {"category": "کفش", "product": "کفش چرمی"},
        "score": 0.84,
        "reason": "پرسش قیمت نشان‌دهنده علاقه به خرید است.",
        "evidence": ["قیمت کفش چنده"],
    })
    context = {"category_decision": {
        "category_id": 7, "product_id": 9, "category": "کفش", "product": "کفش چرمی",
    }}

    result = evaluate_with_jev(
        "قیمت کفش چنده؟ برای خرید این مدل هم راهنمایی می‌خواهم", post_context=context,
        knowledge_reader=reader, jev=jev,
    )

    assert set(result) == {
        "status", "related", "score", "reason", "indexed_information_used",
    }
    assert result["status"] == "probably a buyer"
    assert result["related"] == {"category": "کفش", "product": "کفش چرمی"}
    assert result["score"] == 0.84
    assert result["reason"]["evidence"] == ["قیمت کفش چنده"]
    assert result["indexed_information_used"]["source"]["document_ids"] == ["doc-1"]
    assert result["indexed_information_used"]["answers"] == reader.snapshot.answers
    assert result["indexed_information_used"]["indexed_information"] == reader.snapshot.indexed_information
    assert reader.calls[0]["category_id"] == "7"
    assert reader.calls[0]["product_id"] == "9"
    assert jev.calls[0]["comment_text"] == "قیمت کفش چنده؟ برای خرید این مدل هم راهنمایی می‌خواهم"
    assert jev.calls[0]["knowledge"] == reader.snapshot


def test_unsupported_or_unverifiable_jev_decision_is_uncertain():
    reader = StubKnowledgeReader()
    jev = StubJev({
        "status": "probably a buyer", "score": 0.99,
        "reason": "The comment is relevant.", "evidence": ["کفش"] * 1,
    })

    result = evaluate_with_jev(
        "قیمت چند است؟", post_context={}, knowledge_reader=reader, jev=jev,
    )

    assert result["status"] == "uncertain"
    assert result["score"] == 0.0
    assert result["reason"]["evidence"] == ["قیمت چند است؟"]


def test_long_ambiguous_comment_with_confident_jev_score_goes_to_review():
    reader = StubKnowledgeReader()
    jev = StubJev({
        "status": "probably a buyer",
        "score": 0.99,
        "reason": "The comment seems positive.",
        "evidence": ["این مدل را دیدم"],
    })
    text = "این مدل را دیدم، شاید بعداً درباره‌اش فکر کنم."

    result = evaluate_with_jev(
        text, post_context={}, knowledge_reader=reader, jev=jev,
    )

    assert result["status"] == "uncertain"
    assert result["score"] == 0.99
    assert "human review" in result["reason"]["text"]
    assert result["reason"]["evidence"] == ["این مدل را دیدم"]


def test_missing_knowledge_or_jev_fails_closed_to_uncertain():
    result = evaluate_with_jev(
        "قیمت کفش چنده؟", post_context={}, knowledge_reader=None, jev=None,
    )

    assert result["status"] == "uncertain"
    assert result["reason"]["evidence"] == ["قیمت کفش چنده؟"]
    assert result["indexed_information_used"]["source"] == {"kind": "unavailable"}


def test_prefilter_sets_aside_links_follow_spam_and_existing_advertising_cues():
    assert prefilter_comment("این لینک را ببینید https://spam.example").reason == "link_spam"
    assert prefilter_comment("follow for follow").reason == "follow_for_follow"
    result = prefilter_comment("پیج منم کفش می‌فروشم، موجود دارم")
    assert result.status == "set_aside"
    assert result.evidence


def test_prefilter_keeps_buyer_question_evaluable():
    result = prefilter_comment("قیمت کفش چنده؟")

    assert result.status == "eligible"
    assert result.reason == "passed_prefilter"
