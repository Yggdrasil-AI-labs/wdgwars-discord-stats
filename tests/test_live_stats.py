"""Unit tests for live_stats_channels.py.

Pure-logic and network-mocked tests only: no real Discord or WDGoWars calls.
Run: python -m pytest tests/ (or pytest --cov=live_stats_channels).
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import live_stats_channels as m  # noqa: E402


class FakeResp:
    """Minimal stand-in for a urllib response used as a context manager."""

    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FormattingTests(unittest.TestCase):
    def test_fmt_int_space_separator(self):
        self.assertEqual(m.fmt_int(1234567), "1 234 567")
        self.assertEqual(m.fmt_int(0), "0")
        self.assertEqual(m.fmt_int(1000), "1 000")

    def test_fmt_int_non_numeric_falls_back_to_str(self):
        self.assertEqual(m.fmt_int("nope"), "nope")
        self.assertEqual(m.fmt_int(None), "None")

    def test_label_of(self):
        self.assertEqual(m.label_of("\U0001f4ca Total: 1 234"), "Total")
        self.assertEqual(m.label_of("\U0001f7e2 API: UP (5ms)"), "API")
        self.assertEqual(m.label_of("API"), "API")  # no colon
        self.assertEqual(m.label_of("Total: 5"), "Total")


class RankTests(unittest.TestCase):
    def test_rank_prefers_all_time(self):
        self.assertEqual(m.rank_str({"your_rank": {"all_time": 42}}), "#42 all time")

    def test_rank_falls_through_to_week(self):
        self.assertEqual(
            m.rank_str({"your_rank": {"all_time": None, "week": 5}}), "#5 week")

    def test_rank_outside_window_shows_top_n(self):
        self.assertEqual(m.rank_str({}), ">100")
        self.assertEqual(m.rank_str({"your_rank": {"top_n": 50}}), ">50")


class FindGangTests(unittest.TestCase):
    def test_found_returns_rank_and_entry(self):
        lb = {"gangs": [{"name": "A"}, {"name": "B", "member_count": 5}]}
        rank, entry = m.find_gang(lb, "B")
        self.assertEqual(rank, 2)
        self.assertEqual(entry["member_count"], 5)

    def test_not_found_and_edge_cases(self):
        lb = {"gangs": [{"name": "A"}]}
        self.assertEqual(m.find_gang(lb, "C"), (None, None))
        self.assertEqual(m.find_gang(lb, "-"), (None, None))
        self.assertEqual(m.find_gang(None, "A"), (None, None))


class FootprintTests(unittest.TestCase):
    def test_sum_aps_from_sample_cells(self):
        self.assertEqual(m.footprint_aps(m.SAMPLE_CELLS), 156)

    def test_invalid_or_missing_returns_none(self):
        self.assertIsNone(m.footprint_aps(None))
        self.assertIsNone(m.footprint_aps({}))
        self.assertIsNone(m.footprint_aps({"ok": False, "cells": []}))
        self.assertIsNone(m.footprint_aps({"ok": True}))  # no cells key
        self.assertIsNone(m.footprint_aps({"ok": True, "cells": "nope"}))

    def test_skips_malformed_rows(self):
        cells = {"ok": True, "cells": [{"aps": 5}, {"aps": "x"}, "nope", {"lat": 1}]}
        self.assertEqual(m.footprint_aps(cells), 5)


class FieldVisibilityTests(unittest.TestCase):
    def test_field_enabled_respects_config(self):
        cfg = {"fields": {"BLE": False}}
        self.assertFalse(m.field_enabled(cfg, "BLE"))
        self.assertTrue(m.field_enabled(cfg, "WiFi"))  # missing = shown

    def test_render_panel_lists_fields(self):
        text = m.render_panel_text({"fields": {}})
        self.assertIn("live-stats fields", text)
        self.assertIn("Total", text)
        self.assertIn("BLE", text)


class ScrubTests(unittest.TestCase):
    def test_scrub_redacts_token_and_key(self):
        orig_token, orig_key = m.TOKEN, m.WDGO_KEY
        try:
            m.TOKEN = "bottoken123"
            m.WDGO_KEY = "apikey456"
            out = m.scrub("see bottoken123 and apikey456 here")
            self.assertNotIn("bottoken123", out)
            self.assertNotIn("apikey456", out)
            self.assertIn("<redacted>", out)
        finally:
            m.TOKEN, m.WDGO_KEY = orig_token, orig_key


class GatherStatsTests(unittest.TestCase):
    def test_sample_returns_stats_and_api_ok_true(self):
        stats, api_ok = m.gather_stats(sample=True)
        self.assertTrue(api_ok)
        self.assertIn("Total", stats)
        self.assertTrue(stats["Total"].endswith("104 063"))
        self.assertIn("API", stats)
        # Sample driver is in a gang that appears on the sample leaderboard.
        self.assertIn("Gang Size", stats)
        self.assertIn("Gang APs", stats)
        # Footprint is summed from the sample cells payload.
        self.assertIn("Footprint", stats)
        self.assertTrue(stats["Footprint"].endswith("156 APs"))

    def test_api_down_reports_not_ok(self):
        orig_key = m.WDGO_KEY
        try:
            m.WDGO_KEY = "dummy"
            m.wdgo_api  # ensure attribute exists
            import unittest.mock as mock
            with mock.patch.object(m, "wdgo_api", return_value=(None, 12, 0)):
                stats, api_ok = m.gather_stats(sample=False)
            self.assertFalse(api_ok)
            self.assertIn("DOWN", stats["API"])
        finally:
            m.WDGO_KEY = orig_key


class WdgoApiTests(unittest.TestCase):
    def test_wdgo_api_parses_json(self):
        import unittest.mock as mock
        orig_key = m.WDGO_KEY
        try:
            m.WDGO_KEY = "k"
            body = json.dumps({"ok": True, "username": "X"}).encode()
            with mock.patch.object(m.urllib.request, "urlopen",
                                   return_value=FakeResp(body, 200)):
                data, latency, status = m.wdgo_api("/endpoint/me")
            self.assertEqual(data["username"], "X")
            self.assertEqual(status, 200)
            self.assertGreaterEqual(latency, 0)
        finally:
            m.WDGO_KEY = orig_key

    def test_wdgo_api_no_key_short_circuits(self):
        orig_key = m.WDGO_KEY
        try:
            m.WDGO_KEY = ""
            self.assertEqual(m.wdgo_api("/endpoint/me"), (None, 0, 0))
        finally:
            m.WDGO_KEY = orig_key


class DiscordApiTests(unittest.TestCase):
    def test_discord_api_returns_parsed_json(self):
        import unittest.mock as mock
        body = json.dumps({"id": "999"}).encode()
        with mock.patch.object(m.urllib.request, "urlopen",
                               return_value=FakeResp(body, 200)):
            out = m.discord_api("GET", "/users/@me")
        self.assertEqual(out["id"], "999")

    def test_discord_api_network_error_returns_none(self):
        import unittest.mock as mock
        with mock.patch.object(m.urllib.request, "urlopen",
                               side_effect=m.urllib.error.URLError("boom")):
            self.assertIsNone(m.discord_api("GET", "/users/@me"))


class TickApiDownTests(unittest.TestCase):
    def test_api_down_tick_refreshes_only_api_channel(self):
        import unittest.mock as mock
        cat = m.CATEGORY_NAME
        channels = [
            {"type": 4, "name": cat, "id": "cat"},
            {"type": 2, "name": "\U0001f7e2 API: UP (5ms)", "parent_id": "cat", "id": "a"},
            {"type": 2, "name": "\U0001f4ca Total: 999", "parent_id": "cat", "id": "t"},
        ]
        patched = []

        def fake_api(method, path, body=None):
            if method == "GET" and path.endswith("/channels"):
                return channels
            if method == "PATCH":
                patched.append(path)
                return {}
            return {}

        down_stats = {"API": "\U0001f534 API: DOWN (HTTP 0)", "Total": "\U0001f4ca Total: 0"}
        with mock.patch.object(m, "gather_stats", return_value=(down_stats, False)), \
             mock.patch.object(m, "load_config", return_value={"fields": {}}), \
             mock.patch.object(m, "poll_reactions", return_value=False), \
             mock.patch.object(m, "discord_api", side_effect=fake_api), \
             mock.patch.object(m, "save_json"):
            state = {"tick": 7}
            m.tick(state, sample=False)

        self.assertEqual(state["tick"], 8)
        # Only the API channel was renamed; the data channel was left alone.
        self.assertEqual(patched, ["/channels/a"])


if __name__ == "__main__":
    unittest.main()
