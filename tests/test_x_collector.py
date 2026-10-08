from __future__ import annotations

import json
import sqlite3
import shutil
import unittest
import io
import contextlib
from pathlib import Path
from unittest.mock import patch

from analysis.store import AnalysisStore
from sources.file_adapter import load_file
from workers.x_collector.client import XCliClient, XCliError
from workers.x_collector.worker import SeenStore, XCollector, normalize_tweet, collect_forever
from workers.x_collector.__main__ import main
from buyer_engine.sources import XFileAdapter, XAdapter, build_x_adapter


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
        self.root = Path.cwd() / "tests" / ".x_collector_test_data"
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
        self.assertEqual(record["id"], "mock-x-1001")
        self.assertEqual(record["author_handle"], "@cafe_buyer_demo")
        self.assertEqual(record["url"], "https://x.com/cafe_buyer_demo/status/mock-x-1001")
        self.assertEqual(record["metadata"]["raw_cli"], self.fixture[0])

    def test_jsonl_loads_as_x_messages_with_source_override(self):
        result = self.collector().run(["اسپرسوساز"])
        messages = load_file(result["output"], default_source="file")
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0].uid, "x:mock-x-1001")
        self.assertEqual(messages[0].source, "x")

    def test_collected_count_matches_loader_for_supported_timestamps(self):
        timestamp_values = ["2026-10-01T10:00:00Z", 1790848800, 1790848800000, "Thu Oct 01 10:00:00 +0000 2026"]
        records = [{**self.fixture[0], "id": str(index), "created_at": value} for index, value in enumerate(timestamp_values)]
        result = self.collector(client=FakeClient(records)).run(["q"])
        messages = load_file(result["output"], default_source="x")
        self.assertEqual(result["collected"], len(messages))
        self.assertEqual(len(messages), len(timestamp_values))
        self.assertTrue(all(message.created_at.utcoffset().total_seconds() == 0 for message in messages))

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
        completed = type("Completed", (), {"returncode": 0, "stdout": json.dumps(self.fixture), "stderr": ""})()
        with patch("workers.x_collector.client.shutil.which", return_value="C:/bin/twitter-cli"), patch("workers.x_collector.client.subprocess.run", return_value=completed) as run, patch.dict("os.environ", {"TWITTER_AUTH_TOKEN": "secret-a", "TWITTER_CT0": "secret-b"}):
            records = XCliClient().search("فارسی", 5)
        self.assertEqual(len(records), 2)
        self.assertFalse(run.call_args.kwargs["shell"])
        self.assertNotIn("secret-a", str(run.call_args))

    def test_query_starting_with_dash_cannot_become_a_cli_option(self):
        completed = type("Completed", (), {"returncode": 0, "stdout": json.dumps(self.fixture), "stderr": ""})()
        with patch("workers.x_collector.client.shutil.which", return_value="C:/bin/twitter-cli"), patch("workers.x_collector.client.subprocess.run", return_value=completed) as run, patch.dict("os.environ", {"TWITTER_AUTH_TOKEN": "token", "TWITTER_CT0": "cookie"}):
            XCliClient().search("--help", 5)
        self.assertEqual(run.call_args.args[0][2], "--query=--help")

    def test_catalog_derives_queries_when_query_file_missing(self):
        path = self.root / "catalog.json"
        path.write_text(json.dumps({"products": [{"name": "محصول یک", "keywords": ["کلمه فارسی", "query"]}]}, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(XCollector.load_queries(self.root / "missing.txt", path), ["کلمه فارسی", "query"])

    def test_buyer_engine_file_adapter_reads_daily_worker_output(self):
        output_path = self.root / "collected" / "2026-10-01.jsonl"
        output_path.parent.mkdir(parents=True)
        record = normalize_tweet(self.fixture[0])
        output_path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
        adapter = XFileAdapter(path=str(self.root / "collected" / "latest.jsonl"))
        found = adapter.search('"دستگاه اسپرسوساز"')
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].source, "x")

    def test_api_backend_remains_the_default(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertIsInstance(build_x_adapter(), XAdapter)

    def test_cli_backend_is_rejected_by_factory(self):
        with patch.dict("os.environ", {"X_BACKEND": "cli"}):
            with self.assertRaises(ValueError):
                build_x_adapter()

    def test_unknown_backend_is_rejected_by_factory(self):
        with patch.dict("os.environ", {"X_BACKEND": "typo"}):
            with self.assertRaises(ValueError):
                build_x_adapter()

    def test_mock_and_enqueue_are_refused(self):
        with self.assertRaises(SystemExit) as error:
            main(["--mock", "--enqueue", "--once"])
        self.assertEqual(error.exception.code, 2)

    def test_dry_run_prints_exact_cli_command_without_secrets(self):
        query_file = self.root / "queries.txt"
        query_file.write_text("-query فارسی\n", encoding="utf-8")
        output = io.StringIO()
        with patch.dict("os.environ", {"TWITTER_AUTH_TOKEN": "private", "TWITTER_CT0": "private-cookie"}), contextlib.redirect_stderr(output), contextlib.redirect_stdout(io.StringIO()):
            result = main(["--dry-run", "--queries", str(query_file)])
        self.assertEqual(result, 0)
        self.assertIn('twitter-cli search "--query=-query فارسی" --limit 50 --json', output.getvalue())
        self.assertNotIn("private", output.getvalue())

    def test_loop_stops_on_failure_and_passes_enqueue(self):
        collector = self.collector()
        collector.run = lambda *args, **kwargs: {"status": "AUTH_FAILED", "collected": 0, "enqueue": kwargs.get("enqueue")}
        with patch("workers.x_collector.worker.time.sleep", return_value=None):
            with self.assertRaises(XCliError):
                collect_forever(collector, ["q"], 1, enqueue=True)


if __name__ == "__main__":
    unittest.main()
