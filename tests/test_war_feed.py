"""Unit tests for war_feed.py.

Pure-logic and network-mocked tests only: no real WDGWars or Discord calls.
"""
from __future__ import annotations

import sys
import unittest
import unittest.mock as mock
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import war_feed as wf  # noqa: E402

NOW = datetime(2026, 7, 19, 16, 0, 0, tzinfo=timezone.utc)


class ParseTsTests(unittest.TestCase):
    def test_postgres_timestamptz_with_microseconds_and_2digit_offset(self):
        dt = wf.parse_ts("2026-07-17 02:32:02.854003+00")
        self.assertIsNotNone(dt)
        self.assertEqual((dt.year, dt.month, dt.day, dt.hour), (2026, 7, 17, 2))
        self.assertIsNotNone(dt.tzinfo)

    def test_iso_z_form(self):
        dt = wf.parse_ts("2026-07-17T02:32:02Z")
        self.assertEqual((dt.year, dt.month, dt.day), (2026, 7, 17))

    def test_offset_hours(self):
        dt = wf.parse_ts("2026-07-17 02:00:00-05")
        self.assertEqual(dt.utcoffset().total_seconds(), -5 * 3600)

    def test_date_only(self):
        dt = wf.parse_ts("2026-07-17")
        self.assertEqual((dt.year, dt.month, dt.day), (2026, 7, 17))

    def test_bad_inputs_return_none(self):
        for bad in (None, "", 123, "not a date", "  "):
            self.assertIsNone(wf.parse_ts(bad))


class CaptureKeyTests(unittest.TestCase):
    def test_stable_for_same_capture(self):
        c = {"when": "t", "lat": 1.0, "lng": 2.0, "ap_count": 5,
             "defender_gang_id": 7}
        self.assertEqual(wf._capture_key(c), wf._capture_key(dict(c)))

    def test_distinct_for_different_captures(self):
        a = {"when": "t", "lat": 1.0, "lng": 2.0, "ap_count": 5}
        b = {"when": "t", "lat": 1.0, "lng": 2.0, "ap_count": 6}
        self.assertNotEqual(wf._capture_key(a), wf._capture_key(b))


class DetectCapturesTests(unittest.TestCase):
    def test_new_capture_is_announced_once(self):
        me = {"ok": True, "recent_captures": [
            {"when": "t2", "lat": 1.0, "lng": 2.0, "ap_count": 10,
             "defender_gang": "Foe", "defender_gang_id": 3}]}
        state = {"seen_captures": []}
        embeds = wf.detect_captures(me, state)
        self.assertEqual(len(embeds), 1)
        self.assertIn("Captured 10 APs", embeds[0]["title"])
        self.assertIn("Foe", embeds[0]["description"])
        # second pass with the same capture already seen -> nothing
        self.assertEqual(wf.detect_captures(me, state), [])

    def test_unclaimed_when_no_defender(self):
        me = {"ok": True, "recent_captures": [
            {"when": "t", "lat": 1.0, "lng": 2.0, "ap_count": 1,
             "defender_gang": None}]}
        embeds = wf.detect_captures(me, {"seen_captures": []})
        self.assertIn("unclaimed territory", embeds[0]["description"])
        self.assertIn("Captured 1 AP", embeds[0]["title"])
        self.assertNotIn("1 APs", embeds[0]["title"])  # singular

    def test_oldest_first_ordering(self):
        # recent_captures is newest-first; a batch should read oldest-first.
        me = {"ok": True, "recent_captures": [
            {"when": "new", "lat": 1, "lng": 1, "ap_count": 2},
            {"when": "old", "lat": 2, "lng": 2, "ap_count": 3}]}
        embeds = wf.detect_captures(me, {"seen_captures": []})
        self.assertEqual([e["fields"][1]["value"] for e in embeds], ["old", "new"])

    def test_missing_array_returns_empty(self):
        self.assertEqual(wf.detect_captures({"ok": True}, {}), [])

    def test_seen_list_is_capped(self):
        old = wf.SEEN_CAP
        wf.SEEN_CAP = 5
        try:
            state = {"seen_captures": [f"stale{i}" for i in range(10)]}
            me = {"ok": True, "recent_captures": [
                {"when": "n", "lat": 1, "lng": 1, "ap_count": 1}]}
            wf.detect_captures(me, state)
            self.assertLessEqual(len(state["seen_captures"]), 5)
        finally:
            wf.SEEN_CAP = old


class DetectLossesTests(unittest.TestCase):
    def _cells(self, rows):
        return {"ok": True, "cells": rows}

    def test_no_previous_state_seeds_quietly(self):
        state = {}
        embeds = wf.detect_losses(
            self._cells([{"lat": 1, "lng": 1, "aps": 10}]), state)
        self.assertEqual(embeds, [])
        self.assertEqual(state["cells"], {wf._cell_key(1, 1): 10})

    def test_shrunk_and_vanished_cells_aggregate(self):
        state = {"cells": {wf._cell_key(1, 1): 40,   # shrinks to 38
                           wf._cell_key(2, 2): 25,   # vanishes
                           wf._cell_key(3, 3): 100}}  # unchanged
        embeds = wf.detect_losses(self._cells([
            {"lat": 1, "lng": 1, "aps": 38},
            {"lat": 3, "lng": 3, "aps": 100}]), state)
        self.assertEqual(len(embeds), 1)
        self.assertIn("Lost 27 APs across 2 cells", embeds[0]["title"])
        self.assertIn("cell lost", embeds[0]["description"])

    def test_growth_produces_no_loss(self):
        state = {"cells": {wf._cell_key(1, 1): 10}}
        embeds = wf.detect_losses(
            self._cells([{"lat": 1, "lng": 1, "aps": 50}]), state)
        self.assertEqual(embeds, [])
        self.assertEqual(state["cells"][wf._cell_key(1, 1)], 50)

    def test_bad_payload_returns_empty(self):
        self.assertEqual(wf.detect_losses({"ok": False}, {}), [])
        self.assertEqual(wf.detect_losses({"ok": True, "cells": "x"}, {}), [])


class DetectRigsTests(unittest.TestCase):
    def test_stale_rig_flags_once_then_stays_quiet(self):
        me = {"ok": True, "devices": [
            {"device_name": "Sleipnir", "last_upload": "2026-07-17 02:00:00+00"}]}
        state = {}
        embeds = wf.detect_rigs(me, state, NOW)
        self.assertEqual(len(embeds), 1)
        self.assertIn("Sleipnir stopped uploading", embeds[0]["title"])
        self.assertTrue(state["rigs"]["Sleipnir"]["down"])
        # still down next tick -> no repeat alert
        self.assertEqual(wf.detect_rigs(me, state, NOW), [])

    def test_recovery_when_upload_resumes(self):
        state = {"rigs": {"Sleipnir": {"last_upload": "old", "down": True}}}
        me = {"ok": True, "devices": [
            {"device_name": "Sleipnir", "last_upload": "2026-07-19 15:59:00+00"}]}
        embeds = wf.detect_rigs(me, state, NOW)
        self.assertEqual(len(embeds), 1)
        self.assertIn("uploading again", embeds[0]["title"])
        self.assertFalse(state["rigs"]["Sleipnir"]["down"])

    def test_fresh_rig_is_not_flagged(self):
        me = {"ok": True, "devices": [
            {"device_name": "Cardputer", "last_upload": "2026-07-19 15:55:00+00"}]}
        self.assertEqual(wf.detect_rigs(me, {}, NOW), [])

    def test_unparseable_last_upload_is_skipped(self):
        me = {"ok": True, "devices": [
            {"device_name": "Ghost", "last_upload": "nonsense"}]}
        self.assertEqual(wf.detect_rigs(me, {}, NOW), [])

    def test_threshold_is_configurable(self):
        old = wf.RIG_STALE_HOURS
        wf.RIG_STALE_HOURS = 1
        try:
            me = {"ok": True, "devices": [
                {"device_name": "R", "last_upload": "2026-07-19 14:00:00+00"}]}
            self.assertEqual(len(wf.detect_rigs(me, {}, NOW)), 1)  # 2h > 1h
        finally:
            wf.RIG_STALE_HOURS = old


class BuildEventsTests(unittest.TestCase):
    def test_alert_filter_disables_detectors(self):
        old = wf.ALERTS
        wf.ALERTS = {"captures"}
        try:
            state = {"seen_captures": [], "cells": {wf._cell_key(1, 1): 40},
                     "rigs": {}}
            me = {"ok": True,
                  "recent_captures": [{"when": "t", "lat": 9, "lng": 9,
                                       "ap_count": 5}],
                  "devices": [{"device_name": "R",
                               "last_upload": "2026-07-10 00:00:00+00"}]}
            cells = {"ok": True, "cells": [{"lat": 1, "lng": 1, "aps": 1}]}
            embeds = wf.build_events(me, cells, state, NOW)
            self.assertEqual(len(embeds), 1)  # only the capture
            self.assertIn("Captured", embeds[0]["title"])
        finally:
            wf.ALERTS = old


class PostEmbedsTests(unittest.TestCase):
    def test_batches_in_tens_via_webhook(self):
        calls = []
        with mock.patch.object(wf, "WEBHOOK_URL", "https://hook"), \
             mock.patch.object(wf, "_post",
                               side_effect=lambda *a, **k: calls.append(a) or True):
            ok = wf.post_embeds([{"title": str(i)} for i in range(23)])
        self.assertTrue(ok)
        self.assertEqual(len(calls), 3)  # 10 + 10 + 3

    def test_empty_is_noop(self):
        with mock.patch.object(wf, "_post") as p:
            self.assertTrue(wf.post_embeds([]))
            p.assert_not_called()

    def test_no_target_raises(self):
        with mock.patch.object(wf, "WEBHOOK_URL", ""), \
             mock.patch.object(wf, "BOT_TOKEN", ""), \
             mock.patch.object(wf, "CHANNEL_ID", ""):
            with self.assertRaises(SystemExit):
                wf.post_embeds([{"title": "x"}])

    def test_bot_token_path_used_when_no_webhook(self):
        seen = {}
        def fake_post(url, payload, method="POST", token=""):
            seen["url"], seen["token"] = url, token
            return True
        with mock.patch.object(wf, "WEBHOOK_URL", ""), \
             mock.patch.object(wf, "BOT_TOKEN", "bott"), \
             mock.patch.object(wf, "CHANNEL_ID", "123"), \
             mock.patch.object(wf, "_post", side_effect=fake_post):
            wf.post_embeds([{"title": "x"}])
        self.assertIn("/channels/123/messages", seen["url"])
        self.assertEqual(seen["token"], "bott")


class ScrubTests(unittest.TestCase):
    def test_redacts_each_secret(self):
        with mock.patch.object(wf, "WDGO_KEY", "K"), \
             mock.patch.object(wf, "BOT_TOKEN", "B"), \
             mock.patch.object(wf, "WEBHOOK_URL", "W"):
            self.assertEqual(wf.scrub("K B W"), "<redacted> <redacted> <redacted>")


class TickTests(unittest.TestCase):
    def test_first_run_seeds_and_posts_nothing(self):
        me = {"ok": True, "recent_captures": [
            {"when": "t", "lat": 1, "lng": 1, "ap_count": 5}], "devices": []}
        saved = {}
        with mock.patch.object(wf, "gather", return_value=(me, None)), \
             mock.patch.object(wf, "load_state", return_value={}), \
             mock.patch.object(wf, "save_state",
                               side_effect=lambda s: saved.update(s)), \
             mock.patch.object(wf, "post_embeds") as posted:
            n = wf.tick(sample=False)
        self.assertEqual(n, 0)
        posted.assert_not_called()
        self.assertTrue(saved.get("seeded"))

    def test_second_run_posts_events(self):
        me = {"ok": True, "recent_captures": [
            {"when": "t", "lat": 1, "lng": 1, "ap_count": 5}], "devices": []}
        with mock.patch.object(wf, "gather", return_value=(me, None)), \
             mock.patch.object(wf, "load_state",
                               return_value={"seeded": True, "seen_captures": []}), \
             mock.patch.object(wf, "save_state"), \
             mock.patch.object(wf, "post_embeds", return_value=True) as posted:
            n = wf.tick(sample=False)
        self.assertEqual(n, 1)
        posted.assert_called_once()

    def test_api_down_skips(self):
        with mock.patch.object(wf, "gather", return_value=(None, None)), \
             mock.patch.object(wf, "load_state", return_value={"seeded": True}), \
             mock.patch.object(wf, "post_embeds") as posted:
            self.assertEqual(wf.tick(sample=False), 0)
        posted.assert_not_called()

    def test_failed_post_does_not_save_state(self):
        me = {"ok": True, "recent_captures": [
            {"when": "t", "lat": 1, "lng": 1, "ap_count": 5}], "devices": []}
        with mock.patch.object(wf, "gather", return_value=(me, None)), \
             mock.patch.object(wf, "load_state",
                               return_value={"seeded": True, "seen_captures": []}), \
             mock.patch.object(wf, "save_state") as saved, \
             mock.patch.object(wf, "post_embeds", return_value=False):
            wf.tick(sample=False)
        saved.assert_not_called()


if __name__ == "__main__":
    unittest.main()
