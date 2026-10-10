from __future__ import annotations

import json
import sqlite3
import shutil
import tempfile
import unittest
import io
import contextlib
from pathlib import Path
from unittest.mock import patch

from workers.x_collector.client import XCliClient, XCliError
from workers.x_collector.worker import SeenStore, XCollector, normalize_tweet, collect_forever
from workers.x_collector.query_source import queries_from_products
from workers.x_collector.__main__ import main


def load_file(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


class FakeClient:
    def __init__(self, results=None, failures=0):
        self.results = results or []
        self.failures = failures
        self.calls = []

    def search(self, query, limit):
        self.calls.append((query, limit))
        if self.failures:
            self.failures -= 1
            raise XCliError("simulated transient failure", kind="transient")
        return self.results


class XCollectorTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="x_collector_"))
        self.root.mkdir(parents=True, exist_ok=True)
        for child in self.root.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
        self.fixture = json.loads((Path(__file__).parent / "fixtures" / "x_tweets.json").read_text(encoding="utf-8"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def collector(self, client=None, **kwargs):
        return XCollector(client=client or FakeClient(self.fixture), output_dir=self.root / "collected", state_path=self.root / "state.sqlite3", sleep_min=0, sleep_max=0, sleeper=lambda _: None, jitter=lambda low, high: 0, **kwargs)

    def test_normalization_maps_exact_loader_contract(self):
        record = normalize_tweet(self.fixture[0])
        self.assertEqual(record["source"], "x")
        self.assertEqual(record["id"], "1990000000000001001")
        self.assertEqual(record["author_handle"], "@cafe_buyer_demo")
        self.assertEqual(record["url"], "https://x.com/cafe_buyer_demo/status/1990000000000001001")
        self.assertEqual(record["author_id"], "81001")
        self.assertEqual(record["metadata"]["query"], None)
        self.assertEqual(record["metadata"]["raw_cli"], self.fixture[0])

    def test_normalization_preserves_author_verified_as_boolean(self):
        verified = normalize_tweet({**self.fixture[0], "author": {**self.fixture[0]["author"], "verified": True}})
        unverified = normalize_tweet({**self.fixture[0], "author": {**self.fixture[0]["author"], "verified": False}})
        self.assertIs(verified["author_verified"], True)
        self.assertIs(unverified["author_verified"], False)

    def test_jsonl_records_are_x_messages(self):
        result = self.collector().run(["اسپرسوساز"])
        records = load_file(result["output"])
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["id"], "1990000000000001001")
        self.assertEqual(records[0]["author_id"], "81001")
        self.assertEqual(records[0]["metadata"]["query"], "اسپرسوساز")
        self.assertEqual(records[0]["source"], "x")

    def test_supported_timestamps_are_normalised_to_utc(self):
        timestamp_values = ["2026-10-01T10:00:00Z", 1790848800, 1790848800000, "Thu Oct 01 10:00:00 +0000 2026"]
        records = [{**self.fixture[0], "id": str(index), "created_at": value} for index, value in enumerate(timestamp_values)]
        result = self.collector(client=FakeClient(records)).run(["q"])
        rows = load_file(result["output"])
        self.assertEqual(result["collected"], len(timestamp_values))
        self.assertTrue(all(r["created_at"].endswith("+00:00") for r in rows))

    def test_dedup_and_daily_cap(self):
        collector = self.collector(daily_cap=1)
        first = collector.run(["q"])
        self.assertEqual(first["collected"], 1)
        second = collector.run(["q"])
        self.assertEqual(second["status"], "DAILY_CAP_REACHED")

    def test_deduplicates_records_on_later_run(self):
        collector = self.collector()
        first = collector.run(["q"])
        second = collector.run(["q"])
        self.assertEqual(first["collected"], 2)
        self.assertEqual(second["collected"], 0)

    def test_dry_run_does_not_call_cli(self):
        client = FakeClient(self.fixture)
        collector = self.collector(client)
        result = collector.run(["فارسی query"], dry_run=True)
        self.assertFalse(result["cli_called"])
        self.assertEqual(client.calls, [])
        self.assertFalse((self.root / "collected").exists())
        self.assertFalse((self.root / "state.sqlite3").exists())

    def test_missing_handle_or_created_at_is_handled(self):
        record = normalize_tweet({"id": "1", "text": "متن فارسی"})
        self.assertIsNone(record)
        record = normalize_tweet({"id": "2", "username": "user", "text": "متن فارسی"})
        self.assertIsNone(record["created_at"])

    def test_failed_state_insert_does_not_leave_partial_jsonl_record(self):
        collector = self.collector()
        state = SeenStore(self.root / "state.sqlite3")
        with patch("workers.x_collector.worker.SeenStore", return_value=state), patch.object(state, "add", side_effect=sqlite3.OperationalError("simulated interruption")):
            with self.assertRaises(sqlite3.OperationalError):
                collector.run(["q"])
        output_path = self.root / "collected" / f"{__import__('datetime').date.today().isoformat()}.jsonl"
        lines = output_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        json.loads(lines[0])
        resumed = self.collector().run(["q"])
        self.assertEqual(resumed["collected"], 1)

    def test_mock_mode_does_not_need_cli(self):
        result = self.collector(client=FakeClient()).run(["query"], mock_records=self.fixture)
        self.assertEqual(result["collected"], 2)

    def test_retry_uses_exponential_backoff_then_succeeds(self):
        sleeps = []
        client = FakeClient(self.fixture[:1], failures=2)
        collector = XCollector(client=client, output_dir=self.root / "out", state_path=self.root / "state.sqlite3", sleep_min=0, sleep_max=0, max_retries=3, sleeper=sleeps.append, jitter=lambda low, high: 0)
        result = collector.run(["query"])
        self.assertEqual(result["collected"], 1)
        self.assertEqual(sleeps[:2], [1.0, 2.0])

    def test_circuit_breaker_stops_after_consecutive_failures(self):
        client = FakeClient(failures=20)
        collector = XCollector(client=client, output_dir=self.root / "out", state_path=self.root / "state.sqlite3", max_retries=0, circuit_breaker=2, sleep_min=0, sleep_max=0, sleeper=lambda _: None, jitter=lambda low, high: 0)
        result = collector.run(["q1", "q2", "q3"])
        self.assertEqual(result["status"], "CIRCUIT_OPEN")
        self.assertEqual(len(client.calls), 2)

    def test_rate_limit_sets_cooldown_and_later_run_does_not_call_client(self):
        client = FakeClient()
        def rate_limited(query, limit):
            client.calls.append((query, limit))
            raise XCliError("rate limit", kind="rate_limit")
        client.search = rate_limited
        collector = self.collector(client, max_retries=3)
        first = collector.run(["q"])
        self.assertEqual(first["status"], "RATE_LIMITED")
        second = collector.run(["q"])
        self.assertEqual(second["status"], "RATE_LIMITED")
        self.assertEqual(len(client.calls), 1)

    def test_partial_status_when_a_query_fails(self):
        client = FakeClient(self.fixture[:1], failures=1)
        collector = self.collector(client, max_retries=0)
        result = collector.run(["bad", "good"])
        self.assertEqual(result["status"], "PARTIAL")

    def test_latest_jsonl_is_written(self):
        result = self.collector().run(["q"])
        latest = self.root / "collected" / "latest.jsonl"
        self.assertEqual(len(load_file(latest)), result["collected"])

    def test_cli_uses_argument_list_no_shell_and_does_not_log_credentials(self):
        completed = type("Completed", (), {"returncode": 0, "stdout": json.dumps({"ok": True, "data": self.fixture}), "stderr": ""})()
        with patch("workers.x_collector.client.shutil.which", return_value="C:/bin/twitter-cli"), patch("workers.x_collector.client.subprocess.run", return_value=completed) as run, patch.dict("os.environ", {"TWITTER_AUTH_TOKEN": "secret-a", "TWITTER_CT0": "secret-b"}):
            records = XCliClient().search("فارسی", 5)
        self.assertEqual(len(records), 2)
        self.assertFalse(run.call_args.kwargs["shell"])
        self.assertNotIn("secret-a", str(run.call_args))

    def test_query_starting_with_dash_cannot_become_a_cli_option(self):
        with self.assertRaisesRegex(XCliError, "starting with '-'"):
            XCliClient().search("--help", 5)

    def test_queries_file_ignores_comments_and_duplicates(self):
        path = self.root / "queries.txt"
        path.write_text("# comment\nکلمه فارسی\nquery\nکلمه فارسی\n", encoding="utf-8")
        self.assertEqual(XCollector.load_queries(path), ["کلمه فارسی", "query"])
        self.assertEqual(XCollector.load_queries(self.root / "missing.txt"), [])

    def test_dry_run_prints_exact_cli_command_without_secrets(self):
        query_file = self.root / "queries.txt"
        query_file.write_text("-query فارسی\n", encoding="utf-8")
        output = io.StringIO()
        with patch.dict("os.environ", {"TWITTER_AUTH_TOKEN": "private", "TWITTER_CT0": "private-cookie"}), contextlib.redirect_stderr(output), contextlib.redirect_stdout(io.StringIO()):
            result = main(["--dry-run", "--queries", str(query_file)])
        self.assertEqual(result, 0)
        self.assertNotIn("twitter search", output.getvalue())
        self.assertIn("Skipping query starting with '-'", output.getvalue())
        self.assertNotIn("private", output.getvalue())

    def test_loop_stops_on_failure(self):
        collector = self.collector()
        collector.run = lambda *args, **kwargs: {"status": "AUTH_FAILED", "collected": 0}
        with patch("workers.x_collector.worker.time.sleep", return_value=None):
            with self.assertRaises(XCliError):
                collect_forever(collector, ["q"], 1)

    def test_flag_like_query_is_skipped_but_following_query_runs(self):
        client = FakeClient(self.fixture[:1])
        result = self.collector(client).run(["--help", "coffee"])
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(len(client.calls), 1)
        self.assertRegex(client.calls[0][0], r"^coffee since_time:\d+$")
        self.assertEqual(client.calls[0][1], 50)
        self.assertEqual(result["collected"], 1)

    def test_error_json_codes_are_classified_even_on_nonzero_exit(self):
        cases = [("rate_limited", "rate_limit"), ("not_authenticated", "fatal"), ("service_unavailable", "transient")]
        for code, kind in cases:
            completed = type("Completed", (), {"returncode": 1, "stdout": json.dumps({"ok": False, "error": {"code": code}}), "stderr": ""})()
            with self.subTest(code=code), patch("workers.x_collector.client.shutil.which", return_value="C:/bin/twitter"), \
                    patch("workers.x_collector.client.subprocess.run", return_value=completed), \
                    patch.dict("os.environ", {"TWITTER_AUTH_TOKEN": "token", "TWITTER_CT0": "cookie"}):
                with self.assertRaises(XCliError) as raised:
                    XCliClient().search("coffee", 5)
                self.assertEqual(raised.exception.kind, kind)

    def test_success_envelope_and_retweet_filter(self):
        payload = {"ok": True, "data": [{**self.fixture[0], "isRetweet": False}, {**self.fixture[1], "isRetweet": True}]}
        completed = type("Completed", (), {"returncode": 0, "stdout": json.dumps(payload), "stderr": ""})()
        with patch("workers.x_collector.client.shutil.which", return_value="C:/bin/twitter"), \
                patch("workers.x_collector.client.subprocess.run", return_value=completed), \
                patch.dict("os.environ", {"TWITTER_AUTH_TOKEN": "token", "TWITTER_CT0": "cookie"}):
            records = XCliClient().search("coffee", 5)
        self.assertEqual([row["id"] for row in records], [self.fixture[0]["id"]])

    def test_product_search_queries_include_category_and_are_deduplicated(self):
        from need_engine.schemas import Product

        product = Product(product_id="1", title="قهوه‌ساز صنعتی", category_path="کافه / تجهیزات",
                          category_keywords=["اسپرسوساز", "قهوه", "اسپرسوساز"])
        self.assertEqual(queries_from_products([product]),
                         ["قهوه‌ساز صنعتی", "کافه / تجهیزات", "اسپرسوساز", "قهوه"])

    def test_agent_reach_nested_author_fields_are_normalized(self):
        record = normalize_tweet({"id": "1990000000000001003", "text": "دنبال آسیاب قهوه هستم",
                                  "author": {"id": "81003", "name": "خریدار", "screenName": "buyer"},
                                  "createdAtISO": "2026-10-01T10:00:00Z", "lang": "fa"}, "آسیاب قهوه")
        self.assertEqual(record["author_id"], "81003")
        self.assertEqual(record["author_handle"], "@buyer")
        self.assertEqual(record["author_name"], "خریدار")
        self.assertEqual(record["metadata"]["query"], "آسیاب قهوه")

    def test_tweet_without_stable_author_id_is_rejected(self):
        record = normalize_tweet({"id": "1990000000000001004", "text": "دنبال آسیاب هستم", "username": "buyer"})
        self.assertIsNone(record)

    def test_advertising_copy_is_rejected_but_plain_buyer_post_is_kept(self):
        ad = {"id": "1", "author_id": "90", "username": "brand",
              "text": "فروش ویژه، همین حالا سفارش دهید"}
        buyer = {"id": "2", "author_id": "91", "username": "person",
                 "text": "برای خانه‌ام دنبال قهوه‌ساز می‌گردم"}
        self.assertIsNone(normalize_tweet(ad))
        self.assertIsNotNone(normalize_tweet(buyer))


if __name__ == "__main__":
    unittest.main()
