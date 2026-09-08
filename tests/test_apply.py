from __future__ import annotations

import io
import json
import os
from pathlib import Path
import re
import tempfile
import tomllib
import unittest
from unittest import mock

from agent_mcp.apply import WRITE_AGENTS, Applier
from agent_mcp.cli import main
from agent_mcp.inventory import Inventory, bounded_json
from agent_mcp.tomlwrite import TomlWriteFailure, locate_server_block, remove_server_block, render_server_table


SENTINEL = "SECRET_SENTINEL_DO_NOT_EMIT"
HEADER_SENTINEL = "HEADER_SENTINEL_DO_NOT_EMIT"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def json_write(path: Path, value: object) -> None:
    write(path, json.dumps(value))


def json_read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class ApplyCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.home = self.base / "home"
        self.project = self.base / "workspace" / "project"
        self.config = self.home / ".config"
        self.etc = self.base / "etc"
        self.project.mkdir(parents=True)
        self.home.mkdir(parents=True)
        json_write(self.home / ".claude.json", {
            "numStartups": 3,
            "mcpServers": {
                "local-tool": {"command": "npx", "args": ["-y", "local-tool"], "env": {"TOKEN": SENTINEL}},
                "remote-tool": {"type": "http", "url": "https://mcp.example.invalid/mcp", "headers": {"Authorization": HEADER_SENTINEL}},
                "events": {"type": "sse", "url": "https://mcp.example.invalid/sse"},
                "socket-tool": {"socket": "/run/mcp.sock"},
            },
        })
        json_write(self.home / ".pi" / "agent" / "settings.json", {"packages": ["npm:pi-mcp-adapter@2.0.0"]})

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def inventory(self) -> Inventory:
        return Inventory(
            self.project,
            home=self.home,
            config_home=self.config,
            etc_root=self.etc,
            system_owner_uid=os.getuid(),
            codex_home=self.home / ".codex",
            environment={},
        )

    def definitions(self) -> dict[str, dict]:
        document = self.inventory().scan()
        return {(item["agentId"], item["name"]): item for item in document["definitions"]}

    def definition_id(self, name: str, agent: str = "claude-code") -> str:
        return self.definitions()[(agent, name)]["id"]

    def apply(self, name: str, agents: list[str], state: str) -> dict:
        return Applier(self.inventory()).apply(self.definition_id(name), agents, state)

    def snapshot(self) -> dict[str, bytes]:
        result: dict[str, bytes] = {}
        for path in self.base.rglob("*"):
            if path.is_file() and not path.is_symlink():
                result[str(path)] = path.read_bytes()
        return result

    def test_remove_and_restore_source_definition(self) -> None:
        before = json_read(self.home / ".claude.json")
        identifier = self.definition_id("local-tool")
        removed = Applier(self.inventory()).remove(identifier)
        self.assertTrue(removed["ok"], removed)
        self.assertTrue(removed["changed"])
        self.assertNotIn(SENTINEL, json.dumps({key: value for key, value in removed.items() if key != "payload"}))
        payload = removed["payload"]
        self.assertEqual(payload["agent"], "claude-code")
        self.assertEqual(payload["format"], 2)
        self.assertEqual(payload["name"], "local-tool")
        self.assertEqual(payload["raw"]["env"], {"TOKEN": SENTINEL})
        after = json_read(self.home / ".claude.json")
        self.assertNotIn("local-tool", after["mcpServers"])
        self.assertEqual(after["numStartups"], 3)
        self.assertNotIn(("claude-code", "local-tool"), self.definitions())
        missing = Applier(self.inventory()).remove(identifier)
        self.assertFalse(missing["ok"])
        arguments = ["restore", "--project", str(self.project), "--record-id", "bin:0123456789abcdef0123456789abcdef", "--payload-stdin", "--json"]
        self.assertNotIn(SENTINEL, " ".join(arguments))
        self.assertNotIn(HEADER_SENTINEL, " ".join(arguments))
        stdout = io.StringIO()
        with mock.patch.dict(os.environ, {
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.config),
            "CODEX_HOME": str(self.home / ".codex"),
        }, clear=True), mock.patch("sys.stdin", io.StringIO(json.dumps(payload) + "\n")), mock.patch("sys.stdout", stdout):
            code = main(arguments)
        restored = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertNotIn(SENTINEL, stdout.getvalue())
        self.assertNotIn("payload", restored)
        self.assertTrue(restored["ok"], restored)
        self.assertEqual(json_read(self.home / ".claude.json")["mcpServers"]["local-tool"], before["mcpServers"]["local-tool"])
        self.assertIn(("claude-code", "local-tool"), self.definitions())
        twice = Applier(self.inventory()).restore("", json.dumps(payload))
        self.assertTrue(twice["ok"])
        self.assertFalse(twice["changed"])
        self.assertFalse(Applier(self.inventory()).restore("", "nope")["ok"])
        self.assertFalse(Applier(self.inventory()).restore("", json.dumps({"agent": "x", "path": "/tmp/x", "spec": {}}))["ok"])
        from agent_mcp.cli import parser
        removing = parser().parse_args(["remove", "--project", str(self.project), "--id", identifier, "--json"])
        self.assertEqual((removing.command, removing.id), ("remove", identifier))
        restoring_stdin = parser().parse_args(["restore", "--project", str(self.project), "--record-id", "bin:0123456789abcdef0123456789abcdef", "--payload-stdin", "--json"])
        self.assertTrue(restoring_stdin.payload_stdin)

    def test_remove_and_restore_preserve_a_symlinked_source(self) -> None:
        source = self.home / ".claude.json"
        target = self.base / "vault" / "claude.json"
        target.parent.mkdir(parents=True)
        source.rename(target)
        source.symlink_to(target)
        definition = self.definitions()[("claude-code", "local-tool")]
        self.assertEqual(definition["source"]["realpath"], str(target))
        before = json_read(target)

        removed = Applier(self.inventory()).remove(definition["id"])
        self.assertTrue(removed["ok"], removed)
        self.assertTrue(source.is_symlink())
        self.assertNotEqual(json_read(target), before)
        restored = Applier(self.inventory()).restore("", json.dumps(removed["payload"]))
        self.assertTrue(restored["ok"], restored)
        self.assertTrue(source.is_symlink())
        self.assertEqual(json_read(target), before)

    def test_on_then_off_round_trip_for_every_target(self) -> None:
        for name in ("local-tool", "remote-tool"):
            result = self.apply(name, ["all"], "on")
            encoded = json.dumps(result)
            self.assertNotIn(SENTINEL, encoded)
            self.assertNotIn(HEADER_SENTINEL, encoded)
            self.assertNotIn("https://", encoded)
            self.assertTrue(result["ok"], result)
            self.assertEqual([item["agent"] for item in result["results"]], list(WRITE_AGENTS))
            self.assertEqual(
                {item["agent"]: item["changed"] for item in result["results"]},
                {agent: agent != "claude-code" for agent in WRITE_AGENTS},
            )
            for item in result["results"]:
                self.assertTrue(item["ok"])
                self.assertTrue(all(path.startswith("~/") for path in item["touched"]))

        claude = json_read(self.home / ".claude.json")
        self.assertEqual(claude["numStartups"], 3)
        self.assertNotIn("type", claude["mcpServers"]["local-tool"])

        codex = tomllib.loads((self.home / ".codex" / "config.toml").read_text(encoding="utf-8"))
        self.assertEqual(codex["mcp_servers"]["local-tool"], {"command": "npx", "args": ["-y", "local-tool"], "env": {"TOKEN": SENTINEL}})
        self.assertEqual(codex["mcp_servers"]["remote-tool"], {"url": "https://mcp.example.invalid/mcp", "http_headers": {"Authorization": HEADER_SENTINEL}})

        opencode = json_read(self.config / "opencode" / "opencode.json")
        self.assertEqual(opencode["mcp"]["servers"]["local-tool"], {"type": "local", "command": ["npx", "-y", "local-tool"], "environment": {"TOKEN": SENTINEL}})
        self.assertEqual(opencode["mcp"]["servers"]["remote-tool"], {"type": "remote", "url": "https://mcp.example.invalid/mcp", "headers": {"Authorization": HEADER_SENTINEL}})

        pi = json_read(self.home / ".pi" / "agent" / "mcp.json")
        self.assertEqual(pi["mcpServers"]["local-tool"], {"command": "npx", "args": ["-y", "local-tool"], "env": {"TOKEN": SENTINEL}})

        self.assertEqual(pi["mcpServers"]["remote-tool"], {"url": "https://mcp.example.invalid/mcp", "headers": {"Authorization": HEADER_SENTINEL}})

        copilot = json_read(self.home / ".copilot" / "mcp-config.json")
        self.assertEqual(copilot["mcpServers"]["local-tool"], {"type": "local", "command": "npx", "args": ["-y", "local-tool"], "env": {"TOKEN": SENTINEL}, "tools": ["*"]})
        self.assertEqual(copilot["mcpServers"]["remote-tool"], {"type": "http", "url": "https://mcp.example.invalid/mcp", "headers": {"Authorization": HEADER_SENTINEL}, "tools": ["*"]})


        antigravity = json_read(self.home / ".gemini" / "config" / "mcp_config.json")
        self.assertEqual(antigravity["mcpServers"]["local-tool"], {"command": "npx", "args": ["-y", "local-tool"], "env": {"TOKEN": SENTINEL}})
        self.assertEqual(antigravity["mcpServers"]["remote-tool"], {"serverUrl": "https://mcp.example.invalid/mcp", "headers": {"Authorization": HEADER_SENTINEL}})

        for path in (self.home / ".gemini" / "config" / "mcp_config.json", self.home / ".codex" / "config.toml"):
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertTrue(path.read_text(encoding="utf-8").endswith("\n"))

        listed = self.inventory().scan()
        self.assertNotIn(SENTINEL, bounded_json(listed))
        applied = {(item["agentId"], item["name"]): item["appliedAgents"] for item in listed["definitions"]}
        self.assertEqual(applied[("claude-code", "local-tool")], sorted(WRITE_AGENTS))
        self.assertEqual(applied[("codex", "remote-tool")], sorted(WRITE_AGENTS))
        self.assertEqual(applied[("claude-code", "events")], ["claude-code"])

        second = self.apply("local-tool", ["all"], "on")
        self.assertTrue(second["ok"])
        self.assertFalse(second["changed"])
        self.assertTrue(all(item["message"] == "already present" for item in second["results"]))

        others = [agent for agent in WRITE_AGENTS if agent != "claude-code"]
        for name in ("local-tool", "remote-tool"):
            result = self.apply(name, others, "off")
            self.assertTrue(result["ok"], result)
            self.assertTrue(all(item["changed"] for item in result["results"]))
            self.assertNotIn(SENTINEL, json.dumps(result))

        self.assertEqual((self.home / ".codex" / "config.toml").read_text(encoding="utf-8"), "")
        self.assertEqual(json_read(self.config / "opencode" / "opencode.json"), {"mcp": {"servers": {}}})
        self.assertEqual(json_read(self.home / ".gemini" / "config" / "mcp_config.json"), {"mcpServers": {}})
        self.assertEqual(json_read(self.home / ".claude.json")["mcpServers"]["local-tool"]["env"]["TOKEN"], SENTINEL)
        applied = {(item["agentId"], item["name"]): item["appliedAgents"] for item in self.inventory().scan()["definitions"]}
        self.assertEqual(applied[("claude-code", "local-tool")], ["claude-code"])

        again = self.apply("local-tool", others, "off")
        self.assertTrue(again["ok"])
        self.assertFalse(again["changed"])

    def test_header_secret_restore_uses_stdin_only(self) -> None:
        identifier = self.definition_id("remote-tool")
        removed = Applier(self.inventory()).remove(identifier)
        self.assertTrue(removed["ok"], removed)
        payload = removed["payload"]
        self.assertEqual(payload["raw"]["headers"], {"Authorization": HEADER_SENTINEL})
        arguments = ["restore", "--project", str(self.project), "--record-id", "bin:fedcba9876543210fedcba9876543210", "--payload-stdin", "--json"]
        self.assertNotIn(HEADER_SENTINEL, " ".join(arguments))
        stdout = io.StringIO()
        with mock.patch.dict(os.environ, {
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.config),
            "CODEX_HOME": str(self.home / ".codex"),
        }, clear=True), mock.patch("sys.stdin", io.StringIO(json.dumps(payload) + "\n")), mock.patch("sys.stdout", stdout):
            code = main(arguments)
        self.assertEqual(code, 0, stdout.getvalue())
        self.assertNotIn(HEADER_SENTINEL, stdout.getvalue())
        restored = json_read(self.home / ".claude.json")["mcpServers"]["remote-tool"]
        self.assertEqual(restored["headers"], {"Authorization": HEADER_SENTINEL})

    def test_source_entry_is_never_rewritten_or_removed(self) -> None:
        source = self.home / ".claude.json"
        before = source.read_bytes()
        on = self.apply("local-tool", ["claude-code"], "on")
        self.assertEqual(on["results"][0]["message"], "already present")
        self.assertFalse(on["changed"])
        off = self.apply("local-tool", ["claude-code"], "off")
        self.assertFalse(off["ok"])
        self.assertIn("own source entry", off["results"][0]["message"])
        self.assertEqual(source.read_bytes(), before)

    def test_toml_edit_preserves_unrelated_content_and_quotes_names(self) -> None:
        config = self.home / ".codex" / "config.toml"
        original = (
            'model = "gpt-5"\n'
            '# keep this comment\n'
            '[mcp_servers.other]\n'
            'command = "other"\n'
            '\n'
            '[projects."/tmp/demo"]\n'
            'trust_level = "trusted"\n'
        )
        write(config, original)
        json_write(self.home / ".claude.json", {"mcpServers": {"dotted.name": {"command": "run", "args": ["a\"b"], "env": {"K": "v\\w"}}}})
        result = self.apply("dotted.name", ["codex"], "on")
        self.assertTrue(result["ok"], result)
        text = config.read_text(encoding="utf-8")
        self.assertTrue(text.startswith(original))
        self.assertIn('[mcp_servers."dotted.name"]', text)
        parsed = tomllib.loads(text)
        self.assertEqual(parsed["mcp_servers"]["dotted.name"], {"command": "run", "args": ["a\"b"], "env": {"K": "v\\w"}})
        self.assertEqual(parsed["mcp_servers"]["other"], {"command": "other"})
        self.assertEqual(parsed["projects"]["/tmp/demo"]["trust_level"], "trusted")

        written = config.read_bytes()
        json_write(self.home / ".claude.json", {"mcpServers": {"dotted.name": {"command": "run", "args": ["changed"]}}})
        conflict = self.apply("dotted.name", ["codex"], "on")
        self.assertFalse(conflict["ok"])
        self.assertEqual(conflict["results"][0], {
            "agent": "codex", "ok": False, "changed": False,
            "message": "a different server named dotted.name already exists in mcp_servers; nothing was changed",
            "touched": [],
        })
        self.assertEqual(config.read_bytes(), written)

        removed = self.apply("dotted.name", ["codex"], "off")
        self.assertTrue(removed["ok"])
        self.assertEqual(config.read_text(encoding="utf-8"), original)

    def test_on_refuses_a_different_same_name_json_entry(self) -> None:
        gemini = self.home / ".gemini" / "config" / "mcp_config.json"
        json_write(gemini, {"theme": "dark", "mcpServers": {"local-tool": {"command": "mine", "args": []}}})
        before = gemini.read_bytes()
        result = self.apply("local-tool", ["antigravity"], "on")
        self.assertFalse(result["ok"])
        self.assertFalse(result["changed"])
        self.assertEqual(result["results"][0]["message"], "a different server named local-tool already exists in mcpServers; nothing was changed")
        self.assertEqual(result["results"][0]["touched"], [])
        self.assertEqual(gemini.read_bytes(), before)
        self.assertNotIn(SENTINEL, json.dumps(result))

        json_write(self.home / ".claude.json", {"mcpServers": {SENTINEL: {"command": "run"}}})
        json_write(gemini, {"mcpServers": {SENTINEL: {"command": "other"}}})
        inventory = self.inventory()
        inventory.scan()
        hidden_id = next(item.id for item in inventory.definitions if item.raw_name == SENTINEL and item.agent == "claude")
        hidden = Applier(self.inventory()).apply(hidden_id, ["antigravity"], "on")
        self.assertFalse(hidden["ok"])
        self.assertNotIn(SENTINEL, json.dumps(hidden))
        self.assertRegex(hidden["results"][0]["message"], r"named server-[0-9a-f]{8} already exists")

        off = Applier(self.inventory()).apply(hidden_id, ["antigravity"], "off")
        self.assertTrue(off["ok"])
        self.assertTrue(off["changed"])
        self.assertEqual(json_read(gemini), {"mcpServers": {}})

    def test_toml_refuses_ambiguous_or_unlocatable_tables(self) -> None:
        config = self.home / ".codex" / "config.toml"
        json_write(self.home / ".claude.json", {"mcpServers": {"tool": {"command": "run"}}})
        write(config, 'mcp_servers = { tool = { command = "run" } }\n')
        result = self.apply("tool", ["codex"], "off")
        self.assertFalse(result["ok"])
        self.assertIn("table-not-found", result["results"][0]["message"])
        self.assertEqual(config.read_text(encoding="utf-8"), 'mcp_servers = { tool = { command = "run" } }\n')

        write(config, '[mcp_servers.tool]\ncommand = "one"\n')
        self.assertEqual(locate_server_block('[mcp_servers.tool]\ncommand = "one"\n', "tool"), (0, 2))
        with self.assertRaises(TomlWriteFailure):
            locate_server_block('[mcp_servers.tool]\ncommand = "one"\n[mcp_servers.tool]\n', "tool")
        with self.assertRaises(TomlWriteFailure):
            remove_server_block("", "tool")
        write(config, 'not = toml = at all\n')
        broken = self.apply("tool", ["codex"], "on")
        self.assertFalse(broken["ok"])
        self.assertEqual(broken["results"][0]["message"], "target is not strict TOML")

    def test_toml_emitter_round_trips_supported_scalars(self) -> None:
        block = render_server_table("we ird", {"command": "c", "args": ["x", "y"], "enabled": True, "startup_timeout_sec": 5, "env": {"A": "1"}})
        parsed = tomllib.loads(block)
        self.assertEqual(parsed["mcp_servers"]["we ird"], {"command": "c", "args": ["x", "y"], "enabled": True, "startup_timeout_sec": 5, "env": {"A": "1"}})
        extended = {"weight": 1.5, "empty": {}, "nested": {"items": [1, True, {"key": "value"}]}}
        self.assertEqual(tomllib.loads(render_server_table("x", extended))["mcp_servers"]["x"], extended)
        with self.assertRaises(TomlWriteFailure):
            render_server_table("x", {"bad": None})

    def test_jsonc_is_refused_and_symlink_targets_are_updated(self) -> None:
        copilot = self.home / ".copilot" / "mcp-config.json"
        write(copilot, '{\n  // comment\n  "mcpServers": {}\n}\n')
        before = copilot.read_bytes()
        result = self.apply("local-tool", ["copilot-cli"], "on")
        self.assertFalse(result["ok"])
        self.assertEqual(result["results"][0]["message"], "target is not strict JSON")
        self.assertEqual(copilot.read_bytes(), before)

        target = self.base / "elsewhere.json"
        json_write(target, {"mcpServers": {}})
        gemini = self.home / ".gemini" / "config" / "mcp_config.json"
        gemini.parent.mkdir(parents=True)
        gemini.symlink_to(target)
        linked = self.apply("local-tool", ["antigravity"], "on")
        self.assertTrue(linked["ok"], linked)
        self.assertTrue(gemini.is_symlink())
        self.assertIn("local-tool", json_read(target)["mcpServers"])

        jsonc_only = self.config / "opencode" / "opencode.jsonc"
        write(jsonc_only, '{"mcp": {}} // v1\n')
        self.assertTrue(self.apply("local-tool", ["opencode"], "on")["ok"])
        self.assertTrue((self.config / "opencode" / "opencode.json").exists())

    def test_unsupported_transports_are_refused(self) -> None:
        result = self.apply("events", ["codex", "antigravity"], "on")
        self.assertFalse(result["ok"])
        by_agent = {item["agent"]: item for item in result["results"]}
        self.assertFalse(by_agent["codex"]["ok"])
        self.assertEqual(by_agent["codex"]["message"], "transport sse is not supported by codex")
        self.assertTrue(by_agent["antigravity"]["ok"])
        self.assertEqual(json_read(self.home / ".gemini" / "config" / "mcp_config.json")["mcpServers"]["events"]["serverUrl"], "https://mcp.example.invalid/sse")
        self.assertFalse((self.home / ".codex" / "config.toml").exists())

        websocket = self.apply("socket-tool", ["antigravity"], "on")
        self.assertFalse(websocket["ok"])
        self.assertEqual(websocket["results"], [])
        self.assertEqual(websocket["message"], "transport unix cannot be copied")

    def test_unknown_id_and_agent(self) -> None:
        unknown = Applier(self.inventory()).apply("0" * 24, ["codex"], "on")
        self.assertEqual(unknown, {
            "ok": False, "schemaVersion": 1, "project": "<project>", "changed": False,
            "message": "unknown definition id", "results": [],
        })
        result = self.apply("local-tool", ["cursor"], "on")
        self.assertFalse(result["ok"])
        self.assertEqual(result["results"][0], {"agent": "cursor", "ok": False, "changed": False, "message": "unknown agent", "touched": []})
        aliased = self.apply("local-tool", ["google-antigravity", "antigravity"], "on")
        self.assertEqual([item["agent"] for item in aliased["results"]], ["antigravity"])

    def test_pi_target_follows_enabled_adapter(self) -> None:
        json_write(self.home / ".pi" / "agent" / "settings.json", {"packages": ["pi-codemode-mcp"]})
        result = self.apply("local-tool", ["pi"], "on")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["results"][0]["touched"], ["~/.pi/agent/.mcp.json"])
        json_write(self.home / ".pi" / "agent" / "settings.json", {"packages": []})
        refused = self.apply("local-tool", ["pi"], "on")
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["results"][0]["message"], "Pi has no enabled MCP adapter package")

    def test_opencode_v1_map_is_kept_in_v1_shape(self) -> None:
        opencode = self.config / "opencode" / "opencode.json"
        json_write(opencode, {"mcp": {"existing": {"type": "remote", "url": "https://old.invalid"}}})
        result = self.apply("local-tool", ["opencode"], "on")
        self.assertEqual(result["results"][0]["message"], "written to mcp (v1 map)")
        document = json_read(opencode)
        self.assertEqual(set(document["mcp"]), {"existing", "local-tool"})
        self.assertNotIn("servers", document["mcp"])
        self.assertTrue(self.apply("local-tool", ["opencode"], "off")["changed"])
        self.assertEqual(set(json_read(opencode)["mcp"]), {"existing"})

    def test_list_never_writes_and_backend_has_no_process_or_network_imports(self) -> None:
        self.apply("local-tool", ["all"], "on")
        before = self.snapshot()
        stdout = io.StringIO()
        with mock.patch.dict(os.environ, {"HOME": str(self.home), "CODEX_HOME": str(self.home / ".codex"), "XDG_CONFIG_HOME": str(self.config)}, clear=True), \
             mock.patch("sys.stdout", stdout), \
             mock.patch("agent_mcp.safeio.atomic_write", side_effect=AssertionError("list must not write")):
            self.assertEqual(main(["list", "--project", str(self.project), "--json"]), 0)
        self.assertEqual(self.snapshot(), before)
        self.assertNotIn(SENTINEL, stdout.getvalue())
        package = Path(__file__).parents[1] / "agent_mcp"
        for source in package.glob("*.py"):
            text = source.read_text(encoding="utf-8")
            self.assertNotRegex(text, r"^\s*(?:import|from)\s+(?:subprocess|socket|urllib)\b", source.name)
            self.assertNotRegex(text, r'^\s*"""', source.name)
            self.assertNotRegex(text, r"^\s*#", source.name)

    def test_cli_apply_emits_only_json_and_exit_codes(self) -> None:
        identifier = self.definition_id("local-tool")
        environment = {"HOME": str(self.home), "CODEX_HOME": str(self.home / ".codex")}
        stdout = io.StringIO()
        with mock.patch.dict(os.environ, environment, clear=True), mock.patch("sys.stdout", stdout):
            code = main(["apply", "--project", str(self.project), "--id", identifier, "--agent", "codex", "--agent", "antigravity", "--state", "on", "--json"])
        self.assertEqual(code, 0)
        lines = stdout.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        document = json.loads(lines[0])
        self.assertEqual(document["schemaVersion"], 1)
        self.assertTrue(document["ok"])
        self.assertEqual([item["agent"] for item in document["results"]], ["codex", "antigravity"])
        self.assertNotIn(SENTINEL, stdout.getvalue())
        self.assertTrue(re.fullmatch(r"[\x00-\x7f]*", stdout.getvalue()))

        stdout = io.StringIO()
        with mock.patch.dict(os.environ, environment, clear=True), mock.patch("sys.stdout", stdout):
            code = main(["apply", "--project", str(self.project), "--id", "missing", "--agent", "codex", "--state", "off", "--json"])
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(stdout.getvalue())["ok"])


if __name__ == "__main__":
    unittest.main()
