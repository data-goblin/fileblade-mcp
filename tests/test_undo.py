from copy import deepcopy
from dataclasses import asdict
import json
import os
from pathlib import Path
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from agent_mcp.apply import Applier, ServerSpec
from agent_mcp.inventory import Inventory
from agent_mcp.model import CORE_AGENT_IDS
from agent_mcp.parsers import ParseFailure, parse_json, parse_toml
from agent_mcp.recovery import RecoveryStore


class ExactUndo(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="fileblade-mcp-undo-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / "home"
        self.project = self.root / "project"
        self.home.mkdir()
        self.project.mkdir()
        self.source = self.home / ".codex" / "config.toml"
        self.agent = "codex"

    def inventory(self):
        return Inventory(self.project, home=self.home, config_home=self.home / ".config",
                         codex_home=self.home / ".codex", etc_root=self.root / "etc",
                         system_owner_uid=os.getuid(), environment={})

    def write(self, text):
        self.source.parent.mkdir(parents=True, exist_ok=True)
        self.source.write_text(text)

    def identifier(self):
        inventory = self.inventory()
        inventory.scan()
        return next(row.id for row in inventory.definitions if row.raw_name == "tool"
                    and CORE_AGENT_IDS[row.agent] == self.agent and row.absolute_path == self.source)

    def remove(self, identifier=None):
        return Applier(self.inventory()).remove(identifier or self.identifier())

    def restore(self, payload, record_id=""):
        return Applier(self.inventory()).restore(record_id, json.dumps(payload))

    def mint(self, payload):
        applier = Applier(self.inventory())
        return applier.recovery.write(payload, "fixture", applier.recovery_context())

    def test_codex_round_trip_preserves_unknown_fields_and_source_text(self):
        original = ('model = "fixture"\n\n# retained preface\n[mcp_servers.tool]\n'
                    'command = "printf" # keep this comment\nargs = ["fixture"]\n'
                    'enabled = false\nstartup_timeout_sec = 0.5\ntool_timeout_sec = 42\n'
                    'enabled_tools = ["first"]\nunknown = { enabled = true, weight = 1.25 }\n'
                    '[mcp_servers.tool.env]\nPRIVATE = "fixture-private-value"\n\n'
                    '[mcp_servers.neighbor]\ncommand = "unchanged"\n')
        self.write(original)
        removed = self.remove()
        self.assertTrue(removed["ok"], removed)
        self.assertNotIn("tool", tomllib.loads(self.source.read_text())["mcp_servers"])
        restored = self.restore(removed["payload"])
        self.assertTrue(restored["ok"], restored)
        self.assertEqual(tomllib.loads(self.source.read_text()), tomllib.loads(original))
        self.assertEqual(self.source.read_text(), original)

    def test_prepared_removal_preserves_the_source_and_stale_records_are_refused(self):
        self.write('[mcp_servers.tool]\ncommand = "printf"\n')
        before = self.source.read_bytes()
        identifier = self.identifier()
        prepared = Applier(self.inventory()).remove(identifier, prepare=True)
        self.assertTrue(prepared["ok"], prepared)
        self.assertEqual(self.source.read_bytes(), before)
        self.write('model = "later"\n' + before.decode())
        refused = Applier(self.inventory()).remove(identifier, expected_payload=prepared["payload"])
        self.assertFalse(refused["ok"], refused)
        self.assertTrue(self.source.read_text().startswith('model = "later"'))
        self.source.write_bytes(before)
        committed = Applier(self.inventory()).remove(identifier, expected_payload=prepared["payload"])
        self.assertTrue(committed["ok"], committed)
        self.assertTrue(self.restore(prepared["payload"])["ok"])
        self.assertEqual(self.source.read_bytes(), before)

    def test_codex_restore_preserves_other_later_settings(self):
        self.write('[mcp_servers.tool]\ncommand = "printf"\nenabled = false\nstartup_timeout_sec = 0.5\n')
        original = tomllib.loads(self.source.read_text())
        removed = self.remove()
        self.assertTrue(removed["ok"], removed)
        self.write('model = "later"\n[mcp_servers.neighbor]\ncommand = "keep"\n')
        restored = self.restore(removed["payload"])
        self.assertTrue(restored["ok"], restored)
        after = tomllib.loads(self.source.read_text())
        self.assertEqual(after["model"], "later")
        self.assertEqual(after["mcp_servers"]["neighbor"], {"command": "keep"})
        self.assertEqual(after["mcp_servers"]["tool"], original["mcp_servers"]["tool"])

    def test_stale_id_cannot_remove_or_copy_a_changed_definition(self):
        self.write('[mcp_servers.tool]\ncommand = "printf"\nstartup_timeout_sec = 1\n')
        identifier = self.identifier()
        self.write('[mcp_servers.tool]\ncommand = "printf"\nstartup_timeout_sec = 2\n')
        before = self.source.read_bytes()
        self.assertNotEqual(identifier, self.identifier())
        self.assertFalse(self.remove(identifier)["ok"])
        self.assertFalse(Applier(self.inventory()).apply(identifier, ["claude-code"], "on")["ok"])
        self.assertEqual(self.source.read_bytes(), before)
        self.assertFalse((self.home / ".claude.json").exists())

    def test_last_opencode_v1_entry_restores_into_the_original_map(self):
        self.agent = "opencode"
        self.source = self.home / ".config" / "opencode" / "opencode.json"
        original = {"other": True, "mcp": {"tool": {"type": "local", "command": ["printf", "fixture"], "enabled": False}}}
        self.write(json.dumps(original))
        removed = self.remove()
        self.assertTrue(removed["ok"], removed)
        self.assertEqual(json.loads(self.source.read_text()), {"other": True, "mcp": {}})
        self.assertTrue(self.restore(removed["payload"])["ok"])
        self.assertEqual(json.loads(self.source.read_text()), original)

    def test_flat_copilot_source_is_removed_and_restored_in_place(self):
        self.agent = "copilot-cli"
        self.source = self.project / ".github" / "mcp.json"
        original = {"tool": {"command": "printf", "tools": ["one"], "timeout": 0.5},
                    "neighbor": {"command": "keep"}}
        self.write(json.dumps(original))
        removed = self.remove()
        self.assertTrue(removed["ok"], removed)
        self.assertTrue(removed["changed"], removed)
        self.assertEqual(json.loads(self.source.read_text()), {"neighbor": original["neighbor"]})
        self.assertTrue(self.restore(removed["payload"])["ok"])
        self.assertEqual(json.loads(self.source.read_text()), original)

    def test_retargeted_symlink_refuses_restore_without_writing_either_file(self):
        self.agent = "claude-code"
        self.source = self.project / ".mcp.json"
        self.write(json.dumps({"mcpServers": {"tool": {"command": "printf"}}}))
        target = self.root / "original.json"
        self.source.rename(target)
        self.source.symlink_to(target)
        removed = self.remove()
        self.assertTrue(removed["ok"], removed)
        replacement = self.root / "replacement.json"
        replacement.write_bytes(target.read_bytes())
        self.source.unlink()
        self.source.symlink_to(replacement)
        before = replacement.read_bytes()
        self.assertFalse(self.restore(removed["payload"])["ok"])
        self.assertEqual(replacement.read_bytes(), before)
        self.assertEqual(target.read_bytes(), before)

    def test_json_restores_original_position_and_preserves_later_unrelated_values(self):
        self.agent = "claude-code"
        self.source = self.project / ".mcp.json"
        original = {"mcpServers": {"first": {"command": "one"}, "tool": {"command": "printf", "enabled": False},
                                   "last": {"command": "three"}}, "other": True}
        self.write(json.dumps(original))
        removed = self.remove()
        self.assertTrue(removed["ok"], removed)
        after = json.loads(self.source.read_text())
        after["later"] = {"keep": True}
        self.write(json.dumps(after))
        self.assertTrue(self.restore(removed["payload"])["ok"])
        original["later"] = after["later"]
        self.assertEqual(json.loads(self.source.read_text()), original)
        self.assertEqual(list(json.loads(self.source.read_text())["mcpServers"]), ["first", "tool", "last"])
        before = self.source.read_bytes()
        again = self.restore(removed["payload"])
        self.assertTrue(again["ok"], again)
        self.assertFalse(again["changed"])
        self.assertEqual(self.source.read_bytes(), before)

    def test_restore_conflict_keeps_current_definition_and_does_not_echo_secrets(self):
        self.agent = "claude-code"
        self.source = self.project / ".mcp.json"
        self.write(json.dumps({"mcpServers": {"tool": {"command": "printf", "env": {"KEY": "PRIVATE_SENTINEL"}}}}))
        removed = self.remove()
        self.assertTrue(removed["ok"], removed)
        self.write(json.dumps({"mcpServers": {"tool": {"command": "newer"}}}))
        before = self.source.read_bytes()
        restored = self.restore(removed["payload"])
        self.assertFalse(restored["ok"])
        self.assertNotIn("PRIVATE_SENTINEL", json.dumps(restored))
        self.assertEqual(self.source.read_bytes(), before)

    def test_flat_pi_record_preserves_adapter_settings(self):
        self.agent = "pi"
        self.source = self.home / ".pi" / "agent" / "settings.json"
        self.write(json.dumps({"packages": ["pi-mcp-adapter"]}))
        self.source = self.project / ".pi" / "mcp.json"
        original = {"settings": {"keep": True}, "$schema": "fixture", "tool": {"command": "printf", "enabled": False}}
        self.write(json.dumps(original))
        removed = self.remove()
        self.assertTrue(removed["ok"], removed)
        self.assertTrue(removed["changed"])
        expected = dict(original)
        del expected["tool"]
        self.assertEqual(json.loads(self.source.read_text()), expected)
        self.assertTrue(self.restore(removed["payload"])["ok"])
        self.assertEqual(json.loads(self.source.read_text()), original)

    def test_toml_dates_nested_values_crlf_and_missing_final_newline_round_trip(self):
        original = ('[mcp_servers."tool"]\r\ncommand = "printf"\r\n'
                    'date = 2026-09-05\r\ntime = 12:30:00\r\nstamp = 2026-09-05T12:30:00Z\r\n'
                    'limits = [1, 0.5, true]\r\n[mcp_servers.tool.extra]\r\nempty = {}')
        self.source.parent.mkdir()
        self.source.write_bytes(original.encode())
        removed = self.remove()
        self.assertTrue(removed["ok"], removed)
        self.assertTrue(self.restore(removed["payload"])["ok"])
        self.assertEqual(self.source.read_bytes(), original.encode())
        again = self.restore(removed["payload"])
        self.assertTrue(again["ok"], again)
        self.assertFalse(again["changed"])

    def test_toml_conflict_and_dispersed_tables_are_refused_without_writes(self):
        self.write('[mcp_servers.tool]\ncommand = "printf"\n[mcp_servers.other]\ncommand = "keep"\n'
                   '[mcp_servers.tool.env]\nKEY = "private"\n')
        before = self.source.read_bytes()
        self.assertFalse(self.remove()["ok"])
        self.assertEqual(self.source.read_bytes(), before)
        self.write('[mcp_servers.tool]\ncommand = "printf"\n')
        removed = self.remove()
        self.assertTrue(removed["ok"], removed)
        self.write('[mcp_servers.tool]\ncommand = "replacement"\n')
        before = self.source.read_bytes()
        self.assertFalse(self.restore(removed["payload"])["ok"])
        self.assertEqual(self.source.read_bytes(), before)

    def test_duplicate_json_keys_and_nonfinite_numbers_refuse_before_removal(self):
        self.agent = "claude-code"
        self.source = self.project / ".mcp.json"
        for malformed in ('{"mcpServers":{"tool":{"command":"one","command":"two"}}}',
                          '{"mcpServers":{"tool":{"command":"printf","timeout":NaN}}}'):
            self.write('{"mcpServers":{"tool":{"command":"printf"}}}')
            identifier = self.identifier()
            self.write(malformed)
            self.assertFalse(self.remove(identifier)["ok"])
            self.assertEqual(self.source.read_text(), malformed)

    def test_definition_replaced_after_discovery_is_refused_on_the_second_read(self):
        self.agent = "claude-code"
        self.source = self.project / ".mcp.json"
        self.write('{"mcpServers":{"tool":{"command":"printf"}}}')
        identifier = self.identifier()
        applier = Applier(self.inventory())
        replacement = {"mcpServers": {"tool": {"command": "replacement"}}}
        with patch.object(applier, "read_json_target", return_value=replacement):
            removed = applier.remove(identifier)
        self.assertFalse(removed["ok"], removed)
        self.assertEqual(json.loads(self.source.read_text()), {"mcpServers": {"tool": {"command": "printf"}}})

    def test_malformed_json_records_are_refused_without_source_changes(self):
        self.agent = "claude-code"
        self.source = self.project / ".mcp.json"
        self.write('{"mcpServers":{"tool":{"command":"printf"}}}')
        removed = self.remove()
        self.assertTrue(removed["ok"], removed)
        before = self.source.read_bytes()
        for key, value in (("format", "2"), ("format", 3), ("kind", "toml"), ("path", []), ("target", "relative"),
                           ("name", []), ("container", ["elsewhere"]), ("position", True), ("raw", []), ("definition", "bad")):
            with self.subTest(key=key):
                payload = dict(removed["payload"], **{key: value})
                self.assertFalse(self.restore(payload, self.mint(payload))["ok"])
                self.assertEqual(self.source.read_bytes(), before)

    def test_malformed_toml_records_are_refused_without_source_changes(self):
        self.write('[mcp_servers.tool]\ncommand = "printf"\n')
        removed = self.remove()
        self.assertTrue(removed["ok"], removed)
        before = self.source.read_bytes()
        for key, value in (("offset", True), ("offset", 10000), ("text", []), ("after", []),
                           ("text", removed["payload"]["text"] + '[unrelated]\nkeep = false\n')):
            with self.subTest(key=key):
                payload = dict(removed["payload"], **{key: value})
                self.assertFalse(self.restore(payload, self.mint(payload))["ok"])
                self.assertEqual(self.source.read_bytes(), before)

    def test_forged_payload_cannot_write_outside_the_known_configuration_files(self):
        self.agent = "claude-code"
        self.source = self.project / ".mcp.json"
        self.write(json.dumps({"mcpServers": {"tool": {"command": "printf"}}}))
        removed = self.remove()
        self.assertTrue(removed["ok"], removed)
        outsider = self.home / "unrelated.json"
        outsider.write_text(json.dumps({"keep": True}))
        before = outsider.read_bytes()
        forged = dict(removed["payload"], path=str(outsider), target=str(outsider))
        self.assertFalse(self.restore(forged)["ok"])
        self.assertEqual(outsider.read_bytes(), before)
        stored = self.restore(forged, self.mint(forged))
        self.assertFalse(stored["ok"], stored)
        self.assertEqual(outsider.read_bytes(), before)

    def test_a_project_source_restores_without_a_project_argument(self):
        self.agent = "claude-code"
        self.source = self.project / ".mcp.json"
        original = json.dumps({"mcpServers": {"tool": {"command": "printf"}}})
        self.write(original)
        removed = self.remove()
        self.assertTrue(removed["ok"], removed)
        elsewhere = Inventory(self.home, home=self.home, config_home=self.home / ".config",
                              codex_home=self.home / ".codex", etc_root=self.root / "etc",
                              system_owner_uid=os.getuid(), environment={})
        restored = Applier(elsewhere).restore(removed["recordId"], json.dumps(removed["payload"]))
        self.assertTrue(restored["ok"], restored)
        self.assertEqual(json.loads(self.source.read_text()), json.loads(original))

    def test_a_restored_record_stops_holding_a_place_in_the_store(self):
        self.agent = "claude-code"
        self.source = self.project / ".mcp.json"
        self.write(json.dumps({"mcpServers": {"tool": {"command": "printf"}}}))
        removed = self.remove()
        self.assertTrue(removed["ok"], removed)
        store = RecoveryStore(self.inventory().recovery_directory())
        self.assertEqual(store.live_records(), 1)
        restored = self.restore(removed["payload"], removed["recordId"])
        self.assertTrue(restored["ok"], restored)
        self.assertEqual(store.live_records(), 0)
        again = self.restore(removed["payload"], removed["recordId"])
        self.assertTrue(again["ok"], again)
        self.assertFalse(again["changed"])

    def test_the_store_refuses_a_new_removal_instead_of_evicting_undo_records(self):
        self.agent = "claude-code"
        self.source = self.project / ".mcp.json"
        self.write(json.dumps({"mcpServers": {"tool": {"command": "printf"}}}))
        applier = Applier(self.inventory())
        context = applier.recovery_context()
        for index in range(64):
            applier.recovery.write({"format": 2, "filler": index}, "fixture", context)
        before = self.source.read_bytes()
        refused = self.remove()
        self.assertFalse(refused["ok"], refused)
        self.assertIn("undo store is full", refused["message"])
        self.assertEqual(self.source.read_bytes(), before)

    def test_preparing_then_removing_reuses_one_record(self):
        self.agent = "claude-code"
        self.source = self.project / ".mcp.json"
        self.write(json.dumps({"mcpServers": {"tool": {"command": "printf"}}}))
        prepared = Applier(self.inventory()).remove(self.identifier(), prepare=True)
        self.assertTrue(prepared["ok"], prepared)
        removed = self.remove()
        self.assertTrue(removed["ok"], removed)
        self.assertEqual(prepared["recordId"], removed["recordId"])
        store = RecoveryStore(self.inventory().recovery_directory())
        self.assertEqual(store.live_records(), 1)

    def test_restore_needs_a_prepared_record_and_reports_its_identifier(self):
        self.agent = "claude-code"
        self.source = self.project / ".mcp.json"
        original = json.dumps({"mcpServers": {"tool": {"command": "printf"}}})
        self.write(original)
        prepared = Applier(self.inventory()).remove(self.identifier(), prepare=True)
        self.assertTrue(prepared["ok"], prepared)
        self.assertEqual(len(prepared["recordId"]), 32)
        removed = self.remove()
        self.assertTrue(removed["ok"], removed)
        self.assertEqual(len(removed["recordId"]), 32)
        unknown = self.restore(removed["payload"], "0" * 32)
        self.assertTrue(unknown["ok"], unknown)
        RecoveryStore(self.inventory().recovery_directory()).discard(removed["recordId"])
        RecoveryStore(self.inventory().recovery_directory()).discard(prepared["recordId"])
        after = self.source.read_bytes()
        refused = self.restore(removed["payload"])
        self.assertFalse(refused["ok"], refused)
        self.assertEqual(self.source.read_bytes(), after)

    def test_parser_recursion_and_integer_limits_are_reported_as_refusals(self):
        for parse, source in ((parse_json, b'{"value":' + b'[' * 5000 + b'0' + b']' * 5000 + b'}'),
                              (parse_toml, b'value = ' + b'[' * 5000 + b'0' + b']' * 5000),
                              (parse_json, b'{"value":' + b'1' * 5000 + b'}')):
            with self.assertRaises(ParseFailure):
                parse(source)

    def test_identical_project_aliases_do_not_share_actionable_identity(self):
        self.agent = "claude-code"
        self.source = self.project / ".mcp.json"
        original = '{"mcpServers":{"tool":{"command":"printf"}}}'
        self.write(original)
        identifier = self.identifier()
        seen = {identifier}
        for spelling in ("other", os.fsdecode(b"other-\xff"), "other-\ufffd"):
            other = self.root / spelling
            other.mkdir()
            source = other / ".mcp.json"
            source.write_text(original)
            inventory = Inventory(other, home=self.home, config_home=self.home / ".config",
                                  codex_home=self.home / ".codex", etc_root=self.root / "etc", environment={})
            inventory.scan()
            row = next(row for row in inventory.definitions if row.agent == "claude" and row.raw_name == "tool")
            self.assertNotIn(row.id, seen)
            seen.add(row.id)
            self.assertFalse(Applier(inventory).remove(identifier)["ok"])
            self.assertEqual(source.read_text(), original)
        self.assertEqual(self.source.read_text(), original)


if __name__ == "__main__":
    unittest.main()
