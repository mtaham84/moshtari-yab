import csv
import json
from pathlib import Path
from datetime import date

from instagram_discovery.comment_intent import classify_comment
from shared.records import SourceRecord, to_json_line
from instagram_discovery.labeling import export_labels, report_labels
from workers.ig_collector.backends.base import InstagramComment, InstagramPost
from workers.ig_collector.lexicon import PersianIntentLexicon
from workers.ig_collector.policies import buyer_request, seller_post
from workers.ig_collector.backends.mock import MockInstagramBackend
from workers.ig_collector.worker import InstagramCollector, normalize_comment, validate_seeds

FIXTURE = Path(__file__).parent / "fixtures" / "seller_post_comments.jsonl"


def test_seller_comment_classifier_matches_persian_fixture():
    rows = [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines()]

    assert len(rows) >= 25
    assert any(any("۰" <= char <= "۹" for char in row["text"]) for row in rows)
    assert any(any("٠" <= char <= "٩" for char in row["text"]) for row in rows)
    assert any(any("0" <= char <= "9" for char in row["text"]) for row in rows)
    assert [(classify_comment(row["text"]).label, classify_comment(row["text"]).cue)
            for row in rows] == [(row["label"], row["cue"]) for row in rows]


def test_seller_policy_writes_lead_shape_to_separate_jsonl(tmp_path):
    intent = PersianIntentLexicon.load()
    post = InstagramPost(
        post_id="post-1", shortcode="Seller1", caption="کفش جدید موجود است",
        username="shop_fa", full_name=None, author_id="10", created_at=None,
        like_count=1, comment_count=2, media_type="image", hashtags=[],
    )
    buyer = InstagramComment(
        comment_id="1", text="قیمت؟", username="buyer_fa",
        full_name=None, author_id="20", created_at=None,
    )
    other = InstagramComment(
        comment_id="2", text="فالو بک میدم", username="promoter",
        full_name=None, author_id="21", created_at=None,
    )
    parent = {"id": "ig:Seller1", "text": post.caption, "url": "https://www.instagram.com/p/Seller1/",
              "metadata": {"intent": {"score": 0, "labels": ["advertisement"]}}}
    lead = normalize_comment(buyer, post, "account:shop_fa", "mock", intent)
    ignored = normalize_comment(other, post, "account:shop_fa", "mock", intent)

    accepted = seller_post(lead, parent, 1)

    assert accepted["id"] == "ig:Seller1:c1"
    assert accepted["metadata"]["parent_post"]["caption"] == post.caption
    assert accepted["metadata"]["intent"] == {"label": "price_question", "cue": "قیمت"}
    assert seller_post(ignored, parent, 1) is None
    output = tmp_path / "seller_leads.jsonl"
    output.write_text(to_json_line(SourceRecord.from_dict(accepted)) + "\n", encoding="utf-8")
    stored = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
    assert stored["kind"] == "comment"
    assert stored["metadata"]["parent_post"]["caption"] == post.caption


def test_seller_policy_writes_a_separate_file_without_changing_post_jsonl(tmp_path):
    intent = PersianIntentLexicon.load()
    backend = MockInstagramBackend(Path(__file__).resolve().parents[2] / "workers" / "ig_collector" /
                                   "tests" / "fixtures" / "instagram.json")
    output_dir = tmp_path / "collected"
    collector = InstagramCollector(backend, output_dir=output_dir, state_path=tmp_path / "state.sqlite3",
                                  daily_cap=100, min_intent_score=1, sleep_min=0, sleep_max=0,
                                  sleeper=lambda _: None, jitter=lambda low, high: 0, lexicon=intent)
    seeds = [{"kind": "account", "value": "demo_demand", "limit": 12}]
    comments = {"mode": "all", "policy": "buyer_request", "max_posts_per_run": 5, "max_per_post": 50}
    first_result = collector.run(seeds, comments)
    dated = output_dir / f"{date.today().isoformat()}.jsonl"
    before_flow_a = [json.loads(line) for line in dated.read_text(encoding="utf-8").splitlines()]
    comments["policy"] = "seller_post"

    result = collector.run(seeds, comments)

    assert result["status"] == "COMPLETED"
    assert result["collected"] == 1
    posts = [json.loads(line) for line in dated.read_text(encoding="utf-8").splitlines()] if dated.exists() else []
    leads = [json.loads(line) for line in (output_dir / "seller_leads.jsonl").read_text(encoding="utf-8").splitlines()]
    assert posts[:len(before_flow_a)] == before_flow_a
    assert [row["id"] for row in posts[len(before_flow_a):]] == ["ig:IGAd102"]
    assert [row["id"] for row in leads] == ["ig:IGAd102:cc-5"]
    assert result["seller_leads"] == 1
    assert leads[0]["metadata"]["parent_post"]["id"] == "ig:IGAd102"


def test_buyer_request_policy_keeps_current_numeric_comment_rule():
    post = {"metadata": {"intent": {"labels": ["request"]}}}
    record = {"text": "۱", "metadata": {"intent": {"score": 0, "labels": ["other"]}}}

    accepted = buyer_request(record, post, 1)

    assert accepted["metadata"]["intent"] == {"score": 1, "labels": ["request"]}
    price_comment = {"text": "قیمت؟", "metadata": {"intent": {"score": 0, "labels": ["other"]}}}
    assert buyer_request(price_comment, post, 1) is None


def test_seed_selects_comment_policy_and_defaults_to_buyer_request():
    _, default = validate_seeds({"comments": {"mode": "signals"}})
    _, seller = validate_seeds({"comments": {"mode": "all", "policy": "seller_post"}})

    assert default["policy"] == "buyer_request"
    assert seller["policy"] == "seller_post"
    _, fallback = validate_seeds({"comments": {"mode": "signals", "policy": "seller_post"}})
    assert fallback["mode"] == "all"


def test_label_export_and_report(tmp_path):
    source = tmp_path / "comments.jsonl"
    source.write_text(
        json.dumps({"id": "ig:p1", "kind": "post", "text": "فروش کفش موجود است",
                    "metadata": {"post_id": "p1"}}, ensure_ascii=False) + "\n"
        + json.dumps({"id": "ig:p1:c1", "kind": "comment", "text": "قیمت؟",
                      "metadata": {"post_id": "p1"}}, ensure_ascii=False) + "\n"
        + json.dumps({"id": "ig:p1:c2", "kind": "comment", "text": "دنبال کفش خوب هستم",
                      "metadata": {"post_id": "p1"}}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    labels = tmp_path / "batch.csv"

    assert export_labels(source, labels, sample=200) == 2
    with labels.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert {row["parent caption"] for row in rows} == {"فروش کفش موجود است"}
    assert all(row["flow"] == row["label"] == "" for row in rows)
    rows[0].update(flow="A", label="lead")
    rows[1].update(flow="B", label="lead")
    with labels.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    report = report_labels(labels)

    assert report["leads"]["A"] == 1
    assert report["leads"]["B"] == 1
    assert report["lead_share"] == {"A": 0.5, "B": 0.5}
    assert report["classifier_precision"]["A"] == 1.0
    assert report["classifier_precision"]["B"] == 1.0
