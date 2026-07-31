"""Unit tests for discord_stats_webhook.py.

Pure-logic and network-mocked tests only: no real WDGWars or Discord calls.
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


def _data_names(embed_fields):
    """Field names minus the section-header dividers (headers carry a zero-width
    value; real fields carry data)."""
    return [f["name"] for f in embed_fields if f["value"] != "​"]


class BuildEmbedTests(unittest.TestCase):
    def test_full_sample_has_all_metrics(self):
        embed = w.build_embed(w.SAMPLE_ME)
        e = embed["embeds"][0]
        names = [f["name"] for f in e["fields"]]
        # Account section header first, then the account metrics.
        self.assertEqual(names[0], "📊 Account")
        self.assertEqual(_data_names(e["fields"])[:5],
                         ["Wi-Fi", "BLE", "Aircraft", "MeshCore", "Total"])
        self.assertEqual(e["description"], "Gang: Sample Gang")
        self.assertIn("SampleDriver", e["title"])

    def test_missing_fields_are_dropped_not_zeroed(self):
        embed = w.build_embed({"username": "Z", "wifi": 5})
        e = embed["embeds"][0]
        self.assertEqual(_data_names(e["fields"]), ["Wi-Fi"])  # no ble/aircraft/mesh/total
        self.assertIsNone(e["description"])  # no gang

    def test_thousands_separator_in_values(self):
        embed = w.build_embed({"username": "Z", "wifi": 12345, "total": 12345})
        vals = {f["name"]: f["value"] for f in embed["embeds"][0]["fields"]}
        self.assertEqual(vals["Wi-Fi"], "12,345")

    def test_footprint_adds_territory_section(self):
        embed = w.build_embed({"username": "Z", "wifi": 5}, footprint=3210)["embeds"][0]
        names = [f["name"] for f in embed["fields"]]
        self.assertIn("🌐 Territory", names)
        vals = {f["name"]: f["value"] for f in embed["fields"]}
        self.assertEqual(vals["🗺 Footprint"], "3,210 APs")
        self.assertIn("/api/me/cells", embed["footer"]["text"])

    def test_no_footprint_no_territory_section(self):
        embed = w.build_embed({"username": "Z", "wifi": 5})["embeds"][0]
        names = [f["name"] for f in embed["fields"]]
        self.assertNotIn("🌐 Territory", names)
        self.assertEqual(embed["footer"]["text"], "via /api/me")


class DeviceFieldsTests(unittest.TestCase):
    def test_devices_render_one_field_each(self):
        fields = w.device_fields(w.SAMPLE_ME, limit=25)
        self.assertEqual([f["name"] for f in fields], ["🖥 Cardputer", "🖥 Sleipnir"])
        self.assertIn("11,800 nets", fields[0]["value"])
        self.assertIn("57 uploads", fields[0]["value"])
        self.assertIn("last 2026-07-17", fields[0]["value"])
        # aircraft/mesh only appear when non-zero
        self.assertNotIn("air", fields[0]["value"])
        self.assertIn("90 air", fields[1]["value"])
        self.assertIn("12 mesh", fields[1]["value"])

    def test_no_devices_array_returns_empty(self):
        self.assertEqual(w.device_fields({"username": "Z"}, limit=25), [])
        self.assertEqual(w.device_fields({"devices": "nope"}, limit=25), [])

    def test_limit_caps_field_count(self):
        me = {"devices": [{"device_name": f"d{i}", "networks": i} for i in range(10)]}
        self.assertEqual(len(w.device_fields(me, limit=3)), 3)
        self.assertEqual(w.device_fields(me, limit=0), [])

    def test_unnamed_and_empty_row(self):
        fields = w.device_fields({"devices": [{}]}, limit=25)
        self.assertEqual(fields[0]["name"], "🖥 unnamed")
        self.assertEqual(fields[0]["value"], ", ")

    def test_build_embed_adds_devices_section_and_footer(self):
        embed = w.build_embed(w.SAMPLE_ME)["embeds"][0]
        names = [f["name"] for f in embed["fields"]]
        self.assertIn("🖥 Devices", names)              # section header
        self.assertIn("🖥 Cardputer", names)            # a rig row
        # header comes immediately before the first rig row
        self.assertLess(names.index("🖥 Devices"), names.index("🖥 Cardputer"))
        self.assertIn("per-rig", embed["footer"]["text"])

    def test_build_embed_without_devices_keeps_plain_footer(self):
        embed = w.build_embed({"username": "Z", "wifi": 1})["embeds"][0]
        names = [f["name"] for f in embed["fields"]]
        self.assertNotIn("🖥 Devices", names)
        self.assertEqual(embed["footer"]["text"], "via /api/me")


class FmtLastUploadTests(unittest.TestCase):
    def test_takes_date_from_postgres_and_iso_forms(self):
        self.assertEqual(
            w._fmt_last_upload("2026-07-17 02:32:02.854003+00"), "2026-07-17")
        self.assertEqual(w._fmt_last_upload("2026-07-17T02:32:02Z"), "2026-07-17")

    def test_non_string_and_empty(self):
        self.assertEqual(w._fmt_last_upload(None), "")
        self.assertEqual(w._fmt_last_upload(""), "")
        self.assertEqual(w._fmt_last_upload(123), "")


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


class FetchFootprintTests(unittest.TestCase):
    def test_sums_cell_aps(self):
        import unittest.mock as mock
        body = json.dumps({"ok": True, "cells": [
            {"aps": 40}, {"aps": 100}, {"aps": 16}]}).encode()
        with mock.patch.object(w.urllib.request, "urlopen",
                               return_value=FakeResp(body, 200)):
            self.assertEqual(w.fetch_footprint("k"), 156)

    def test_bad_payload_or_error_returns_none(self):
        import unittest.mock as mock
        with mock.patch.object(w.urllib.request, "urlopen",
                               return_value=FakeResp(b'{"ok": false}', 200)):
            self.assertIsNone(w.fetch_footprint("k"))
        with mock.patch.object(w.urllib.request, "urlopen",
                               side_effect=w.urllib.error.URLError("boom")):
            self.assertIsNone(w.fetch_footprint("k"))


if __name__ == "__main__":
    unittest.main()
