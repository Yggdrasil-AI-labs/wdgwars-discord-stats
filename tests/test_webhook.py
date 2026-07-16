"""Unit tests for discord_stats_webhook.py.

Pure-logic and network-mocked tests only: no real WDGoWars or Discord calls.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import discord_stats_webhook as w  # noqa: E402


class FakeResp:
    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class BuildEmbedTests(unittest.TestCase):
    def test_full_sample_has_all_metrics(self):
        embed = w.build_embed(w.SAMPLE_ME)
        e = embed["embeds"][0]
        names = [f["name"] for f in e["fields"]]
        self.assertEqual(names, ["Wi-Fi", "BLE", "Aircraft", "MeshCore", "Total"])
        self.assertEqual(e["description"], "Gang: Sample Gang")
        self.assertIn("SampleDriver", e["title"])

    def test_missing_fields_are_dropped_not_zeroed(self):
        embed = w.build_embed({"username": "Z", "wifi": 5})
        e = embed["embeds"][0]
        names = [f["name"] for f in e["fields"]]
        self.assertEqual(names, ["Wi-Fi"])  # no ble/aircraft/mesh/total
        self.assertIsNone(e["description"])  # no gang

    def test_thousands_separator_in_values(self):
        embed = w.build_embed({"username": "Z", "wifi": 12345, "total": 12345})
        vals = {f["name"]: f["value"] for f in embed["embeds"][0]["fields"]}
        self.assertEqual(vals["Wi-Fi"], "12,345")


class ScrubTests(unittest.TestCase):
    def test_scrub_redacts_key(self):
        out = w.scrub("key is secret123 here", "secret123")
        self.assertNotIn("secret123", out)
        self.assertIn("<redacted-key>", out)

    def test_scrub_no_key_is_noop(self):
        self.assertEqual(w.scrub("plain text", ""), "plain text")


class FetchMeTests(unittest.TestCase):
    def test_fetch_me_parses_ok_response(self):
        import unittest.mock as mock
        body = json.dumps({"ok": True, "username": "X", "wifi": 1}).encode()
        with mock.patch.object(w.urllib.request, "urlopen",
                               return_value=FakeResp(body, 200)):
            data = w.fetch_me("k")
        self.assertEqual(data["username"], "X")

    def test_fetch_me_rejected_key_raises(self):
        import unittest.mock as mock
        body = json.dumps({"ok": False, "error": "bad key"}).encode()
        with mock.patch.object(w.urllib.request, "urlopen",
                               return_value=FakeResp(body, 200)):
            with self.assertRaises(SystemExit):
                w.fetch_me("k")


if __name__ == "__main__":
    unittest.main()
