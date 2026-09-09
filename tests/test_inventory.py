from __future__ import annotations

import io
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import tempfile
import unittest
from unittest import mock

from agent_mcp.cli import main
from agent_mcp.inventory import (
    MAX_ENVIRONMENT_PATH_CHARS,
    MAX_OUTPUT_BYTES,
    Inventory,
    bounded_json,
    read_environment_path,
)
from agent_mcp.model import safe_label, safe_path
from agent_mcp.parsers import parse_jsonc, parse_toml
from agent_mcp.safeio import artifact_metrics, bounded_read


SENTINEL = "SECRET_SENTINEL_DO_NOT_EMIT"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def json_write(path: Path, value: object) -> None:
    write(path, json.dumps(value))


class InventoryCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.home = self.base / "home"
        self.project = self.base / "workspace" / "project"
        self.config = self.home / ".config"
        self.etc = self.base / "etc"
        self.project.mkdir(parents=True)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def inventory(self, **kwargs: object) -> Inventory:
        kwargs.setdefault("system_owner_uid", os.getuid())
        kwargs.setdefault("codex_home", self.home / ".codex")
        kwargs.setdefault("environment", {})
        return Inventory(
            self.project,
            home=self.home,
            config_home=self.config,
            etc_root=self.etc,
            **kwargs,
        )

    def populate(self) -> None:
        json_write(self.home / ".claude.json", {
            "mcpServers": {
                "shared": {"command": SENTINEL, "args": [SENTINEL], "env": {SENTINEL: SENTINEL}},
                SENTINEL: {"url": "https://" + SENTINEL},
            },
            "projects": {
                str(self.project): {
                    "mcpServers": {"shared": {"command": "local"}},
                    "enabledMcpjsonServers": ["project-only"],
                }
            },
        })
        json_write(self.project / ".mcp.json", {
            "mcpServers": {
                "shared": {"url": "https://" + SENTINEL, "headers": {SENTINEL: SENTINEL}},
                "project-only": {"url": "https://" + SENTINEL},
                "pi-high": {"command": "project"},
            }
        })

        write(self.home / ".codex" / "config.toml", f'''
[mcp_servers.codex-user]
command = "{SENTINEL}"
args = ["{SENTINEL}"]
env = {{ {SENTINEL} = "{SENTINEL}" }}

[projects."{self.project}"]
trust_level = "trusted"

[plugins."codex-plugin@market".mcp_servers.plugin-server]
enabled = false
''')
        codex_plugin = self.home / ".codex" / "plugins" / "cache" / "market" / "codex-plugin" / "1.0.0"
        json_write(codex_plugin / ".codex-plugin" / "plugin.json", {
            "name": "codex-plugin",
            "mcpServers": ".mcp.json",
        })
        json_write(codex_plugin / ".mcp.json", {
            "mcpServers": {"plugin-server": {"url": "https://" + SENTINEL}}
        })

        write(self.config / "opencode" / "opencode.jsonc", '''
        { // user v1
          "mcp": {"open-user": {"type": "remote", "url": "https://example.invalid"}},
        }
        ''')
        json_write(self.project / "opencode.json", {
            "mcp": {"servers": {"open-same": {"type": "local", "command": [SENTINEL]}}}
        })
        json_write(self.project.parent / ".opencode" / "opencode.json", {
            "mcp": {"servers": {"open-same": {"type": "remote", "url": "https://" + SENTINEL}}}
        })

        json_write(self.home / ".pi" / "agent" / "settings.json", {
            "packages": ["npm:pi-mcp-adapter@2.0.0"]
        })
        json_write(self.config / "mcp" / "mcp.json", {
            "mcpServers": {"pi-high": {"command": SENTINEL}}
        })
        json_write(self.project / ".pi" / "mcp.json", {
            "mcpServers": {"pi-high": {"command": SENTINEL, "disabled": True}}
        })

        json_write(self.home / ".copilot" / "mcp-config.json", {
            "mcpServers": {"copilot-same": {"command": SENTINEL}}
        })
        json_write(self.project / ".github" / "mcp.json", {
            "mcpServers": {"copilot-same": {"url": "https://" + SENTINEL}}
        })
        json_write(self.project / ".mcp.json", {
            "mcpServers": {
                "shared": {"url": "https://" + SENTINEL, "headers": {SENTINEL: SENTINEL}},
                "project-only": {"url": "https://" + SENTINEL},
                "pi-high": {"command": "project"},
                "copilot-same": {"command": SENTINEL},
            }
        })
        write(self.home / ".copilot" / "settings.json", '''
        {
          // explicit plugin state
          "enabledPlugins": {"tools@market": true},
          "disabledMcpServers": ["disabled-by-setting"],
        }
        ''')
        copilot_plugin = self.home / ".copilot" / "installed-plugins" / "market" / "tools"
        json_write(copilot_plugin / ".plugin" / "plugin.json", {
            "name": "tools", "mcpServers": ".mcp.json"
        })
        json_write(copilot_plugin / ".mcp.json", {
            "mcpServers": {
                "copilot-same": {"command": SENTINEL},
                "disabled-by-setting": {"command": SENTINEL},
            }
        })

        json_write(self.home / ".gemini" / "config" / "mcp_config.json", {
            "mcpServers": {"anti-same": {"serverUrl": "https://" + SENTINEL}}
        })
        json_write(self.project / ".agents" / "mcp_config.json", {
            "mcpServers": {"anti-same": {"serverUrl": "https://" + SENTINEL, "disabled": True}}
        })

    def test_schema_sources_precedence_and_secret_redaction(self) -> None:
        self.populate()
        with mock.patch.object(socket, "socket", side_effect=AssertionError("network forbidden")), \
             mock.patch.object(subprocess, "Popen", side_effect=AssertionError("spawn forbidden")), \
             mock.patch.object(subprocess, "run", side_effect=AssertionError("spawn forbidden")), \
             mock.patch.object(os, "system", side_effect=AssertionError("spawn forbidden")):
            first = self.inventory().scan()
            second = self.inventory().scan()

        self.assertEqual(first, second)
        encoded = bounded_json(first)
        self.assertNotIn(SENTINEL, encoded)
        self.assertNotIn("https://", encoded)
        self.assertNotIn('"command"', encoded)
        self.assertLessEqual(len(encoded.encode()), MAX_OUTPUT_BYTES)
        self.assertEqual(first["schemaVersion"], 1)
        self.assertEqual(first["healthBasis"], "configuration-only")

        definitions = first["definitions"]
        required = {
            "id", "agent", "name", "scope", "source", "support", "transport", "enabled",
            "trusted", "trustRole", "effective", "selected", "shadowed", "shadowedBy",
            "duplicateGroup", "state", "health", "secretPresence", "metrics", "agentId", "appliedAgents",
        }
        for definition in definitions:
            self.assertTrue(required.issubset(definition))
            self.assertEqual(set(definition["source"]), {"id", "kind", "path", "redacted"})
            self.assertEqual(len(definition["id"]), 24)
            self.assertEqual(definition["health"], "not-probed")
            self.assertFalse({"url", "command", "args", "env", "headers", "token", "bearer"} & set(definition))
            self.assertIsInstance(definition["appliedAgents"], list)
            self.assertIn(definition["agentId"], definition["appliedAgents"])

        def items(agent: str, name: str) -> list[dict[str, object]]:
            return [item for item in definitions if item["agent"] == agent and item["name"] == name]

        claude = items("claude", "shared")
        self.assertEqual([item["scope"] for item in claude if item["selected"] is True], ["local"])
        self.assertTrue(any(item["secretPresence"]["headers"] for item in claude))

        copilot = items("github-copilot-cli", "copilot-same")
        self.assertEqual([item["scope"] for item in copilot if item["selected"] is True], ["plugin"])
        self.assertFalse(items("github-copilot-cli", "disabled-by-setting")[0]["enabled"])

        opencode = items("opencode", "open-same")
        selected_open = [item for item in opencode if item["selected"] is True]
        self.assertEqual(len(selected_open), 1)
        self.assertIn("/.opencode/", selected_open[0]["source"]["path"])

        pi = items("pi", "pi-high")
        self.assertEqual([item["scope"] for item in pi if item["selected"] is True], ["project"])
        self.assertFalse([item for item in pi if item["selected"] is True][0]["enabled"])

        codex_plugin = items("codex", "plugin-server")[0]
        self.assertFalse(codex_plugin["enabled"])
        antigravity = items("google-antigravity", "anti-same")
        self.assertEqual([item["selected"] for item in antigravity], [None, None])
        self.assertTrue(all(item["transport"] == "unknown" for item in antigravity))

    def test_claude_managed_file_is_exclusive(self) -> None:
        json_write(self.home / ".claude.json", {"mcpServers": {"user": {"command": "safe"}}})
        json_write(self.etc / "claude-code" / "managed-mcp.json", {
            "mcpServers": {"managed": {"command": "safe"}}
        })
        definitions = self.inventory().scan()["definitions"]
        by_name = {item["name"]: item for item in definitions if item["agent"] == "claude"}
        self.assertFalse(by_name["user"]["selected"])
        self.assertTrue(by_name["managed"]["selected"])

    def test_pi_core_is_explicitly_unsupported(self) -> None:
        json_write(self.home / ".pi" / "agent" / "settings.json", {"packages": []})
        result = self.inventory().scan()
        pi = next(item for item in result["agents"] if item["id"] == "pi")
        self.assertEqual(pi, {"id": "pi", "support": "unsupported", "reason": "pi-core-has-no-native-mcp"})
        self.assertFalse(any(item["agent"] == "pi" for item in result["definitions"]))

    def test_codex_profiles_are_bounded_unicode_safe_and_never_active(self) -> None:
        profile_name = "配置-日本語-한국어-Ελληνικά-Кириллица-😀-cafe\u0301.config.toml"
        server_name = "中文-日本語-한국어-Ελληνικά-Кириллица-😀-cafe\u0301"
        codex_home = self.home / ".codex"
        write(codex_home / "config.toml", '''
[mcp_servers.collision]
command = "user"
''')
        write(codex_home / profile_name, f'''
[mcp_servers.collision]
command = "profile"

[mcp_servers."{server_name}"]
command = "profile"
''')
        outside = self.base / "outside-profile.config.toml"
        write(outside, '[mcp_servers.symlink-server]\ncommand = "profile"\n')
        (codex_home / "linked.config.toml").symlink_to(outside)

        definitions = [item for item in self.inventory().scan()["definitions"] if item["agent"] == "codex"]
        profile = next(item for item in definitions if item["name"] == server_name)
        self.assertEqual(profile["scope"], "profile")
        self.assertEqual(profile["source"]["path"], f"~/.codex/{profile_name}")
        self.assertFalse(profile["source"]["redacted"])
        self.assertIsNone(profile["enabled"])
        self.assertIsNone(profile["selected"])
        self.assertIsNone(profile["effective"])
        collision = [item for item in definitions if item["name"] == "collision"]
        self.assertEqual([item["scope"] for item in collision if item["selected"] is True], ["user"])
        self.assertEqual([item["scope"] for item in collision if item["selected"] is None], ["profile"])
        self.assertEqual(len({item["duplicateGroup"] for item in collision}), 1)
        self.assertIsNotNone(collision[0]["duplicateGroup"])
        self.assertNotIn("symlink-server", {item["name"] for item in definitions})

    def test_codex_home_environment_is_aliased_not_exposed(self) -> None:
        profile_name = "日本語-😀.config.toml"
        codex_home = self.base / f"external-{SENTINEL}" / "codex"
        write(codex_home / profile_name, '[mcp_servers.profile-server]\ncommand = "profile"\n')
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}, clear=True):
            document = Inventory(
                self.project,
                home=self.home,
                config_home=self.config,
                etc_root=self.etc,
                system_owner_uid=os.getuid(),
            ).scan()
        profile = next(item for item in document["definitions"] if item["name"] == "profile-server")
        self.assertEqual(profile["source"]["path"], f"<codex-home>/{profile_name}")
        self.assertFalse(profile["source"]["redacted"])
        self.assertNotIn(SENTINEL, bounded_json(document))

    def test_environment_paths_preserve_unicode_and_edge_whitespace(self) -> None:
        segment = "  路  径-日本語-한국어-Ελληνικά-Кириллица-😀-cafe\u0301  "
        codex_home = self.base / segment / "  codex  "
        write(codex_home / "  profile 日本語  .config.toml", '''
[mcp_servers.whitespace-codex]
command = "example"
''')
        environment = {
            "CODEX_HOME": str(codex_home),
        }

        for name, value in environment.items():
            self.assertEqual(read_environment_path(environment, name), value)
        inventory = self.inventory(codex_home=None, environment=environment)
        definitions = inventory.scan()["definitions"]
        self.assertEqual(inventory.codex_home, codex_home.absolute())
        self.assertTrue({
            "whitespace-codex",
        }.issubset({item["name"] for item in definitions}))

    def test_invalid_environment_paths_are_unset_and_inventory_stays_bounded(self) -> None:
        invalid_values: tuple[object, ...] = (
            None,
            "",
            self.base / "non-string",
            "valid-prefix\0invalid-suffix",
            "界" * (MAX_ENVIRONMENT_PATH_CHARS + 1),
        )
        names = (
            "CODEX_HOME",
        )
        exact_limit = "  " + "界" * (MAX_ENVIRONMENT_PATH_CHARS - 4) + "  "
        self.assertEqual(len(exact_limit), MAX_ENVIRONMENT_PATH_CHARS)
        self.assertEqual(read_environment_path({"PATH": exact_limit}, "PATH"), exact_limit)

        for invalid in invalid_values:
            with self.subTest(
                value_type=type(invalid).__name__,
                value_length=len(invalid) if isinstance(invalid, str) else None,
            ):
                environment = {name: invalid for name in names}
                for name in names:
                    self.assertIsNone(read_environment_path(environment, name))
                inventory = Inventory(
                    self.project,
                    home=self.home,
                    config_home=self.config,
                    etc_root=self.etc,
                    system_owner_uid=os.getuid(),
                    environment=environment,
                )
                document = json.loads(bounded_json(inventory.scan()))
                self.assertEqual(document["schemaVersion"], 1)
                self.assertEqual(inventory.codex_home, self.home / ".codex")

        explicit_codex_home = self.base / "  explicit codex 日本語  "
        explicit = self.inventory(
            codex_home=explicit_codex_home,
            environment={"CODEX_HOME": "invalid\0environment"},
        )
        self.assertEqual(explicit.codex_home, explicit_codex_home.absolute())

    def test_opencode_system_is_ignored(self) -> None:
        json_write(self.etc / "opencode" / "opencode.json", {
            "mcp": {"system-opencode": {"type": "local", "command": [SENTINEL]}}
        })
        definitions = self.inventory().scan()["definitions"]
        names = {item["name"] for item in definitions}
        self.assertNotIn("system-opencode", names)

    def test_antigravity_plugins_preserve_unicode_and_redact_secret_paths(self) -> None:
        plugin_name = "插件-日本語-한국어-Ελληνικά-Кириллица-😀-cafe\u0301"
        server_name = "中文-日本語-한국어-Ελληνικά-Кириллица-😀-cafe\u0301"
        plugin_root = self.home / ".gemini" / "antigravity-cli" / "plugins"
        json_write(plugin_root / plugin_name / "mcp_config.json", {
            "mcpServers": {
                server_name: {"command": "example"},
                "collision": {"command": "plugin"},
            }
        })
        json_write(plugin_root / f"plugin-{SENTINEL}" / "mcp_config.json", {
            "mcpServers": {"ordinary-server": {"command": "example"}}
        })
        json_write(self.home / ".gemini" / "config" / "mcp_config.json", {
            "mcpServers": {"collision": {"command": "global"}}
        })
        outside = self.base / "outside-antigravity-plugin"
        json_write(outside / "mcp_config.json", {
            "mcpServers": {"symlink-server": {"command": "example"}}
        })
        (plugin_root / "linked-plugin").symlink_to(outside, target_is_directory=True)

        document = self.inventory().scan()
        definitions = [item for item in document["definitions"] if item["agent"] == "google-antigravity"]
        unicode_definition = next(item for item in definitions if item["name"] == server_name)
        self.assertEqual(
            unicode_definition["source"]["path"],
            f"~/.gemini/antigravity-cli/plugins/{plugin_name}/mcp_config.json",
        )
        self.assertFalse(unicode_definition["source"]["redacted"])
        self.assertIsNone(unicode_definition["enabled"])
        self.assertIsNone(unicode_definition["trusted"])
        self.assertIsNone(unicode_definition["effective"])
        self.assertEqual(
            [item["selected"] for item in definitions if item["name"] == "collision"],
            [None, None],
        )
        secret_source = next(item for item in definitions if item["name"] == "ordinary-server")["source"]
        self.assertTrue(secret_source["redacted"])
        self.assertRegex(secret_source["path"], r"/segment-[0-9a-f]{8}/mcp_config\.json$")
        serialized = bounded_json(document)
        self.assertNotIn(SENTINEL, serialized)
        round_trip = next(
            item for item in json.loads(serialized)["definitions"]
            if item["agent"] == "google-antigravity" and item["name"] == server_name
        )
        self.assertEqual(round_trip["name"], server_name)
        self.assertEqual(round_trip["source"], unicode_definition["source"])
        self.assertNotIn("symlink-server", {item["name"] for item in definitions})

    def test_unicode_policy_rejects_unsafe_categories_dots_and_overlong_values(self) -> None:
        safe = "中文-日本語-한국어-Ελληνικά-Кириллица-😀-cafe\u0301"
        self.assertEqual(safe_label(safe, "1234567890abcdef"), safe)
        self.assertEqual(safe_path("~/" + safe, "source-id"), ("~/" + safe, False))
        unsafe_values = [
            "control-\n", "format-\u200d", "surrogate-\ud800",
            "private-\ue000", "unassigned-\ufdd0", ".", "..", "x" * 129,
            SENTINEL,
        ]
        for value in unsafe_values:
            with self.subTest(value=ascii(value)):
                sanitized, redacted = safe_path("~/" + value, "source-id")
                self.assertTrue(redacted)
                self.assertNotEqual(sanitized, "~/" + value)
                if value not in {".", ".."}:
                    self.assertRegex(safe_label(value, "1234567890abcdef"), r"^server-[0-9a-f]{8}$")

    def test_source_metrics_preserve_unicode_counts_and_dates(self) -> None:
        source = self.project / ".mcp.json"
        text = '{"mcpServers":{"設定":{"command":"中文 日本語 한국어 Ελληνικά Кириллица 😀"}}}'
        write(source, text)
        os.utime(source, (1_700_000_000, 1_700_000_000))

        with mock.patch("agent_mcp.safeio.creation_time", return_value="2026-09-02 12:34"):
            document = self.inventory().scan()
            direct = artifact_metrics(source, text.encode("utf-8"))

        definition = next(item for item in document["definitions"] if item["name"] == "設定")
        metrics = definition["metrics"]
        self.assertEqual(metrics, direct)
        self.assertEqual(metrics["created"], "2026-09-02 12:34")
        self.assertRegex(str(metrics["updated"]), r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")
        self.assertEqual(metrics["bytes"], len(text.encode("utf-8")))
        self.assertEqual(metrics["characters"], len(text))
        self.assertEqual(metrics["words"], len(re.findall(r"\w+", text, re.UNICODE)))
        self.assertEqual(metrics["tokens"], (len(text.encode("utf-8")) + 3) // 4)

    def test_symlinked_regular_files_resolve_with_output_bounds(self) -> None:
        target = self.base / "target.json"
        link = self.base / "link.json"
        write(target, "{}")
        link.symlink_to(target)
        self.assertEqual(bounded_read(link, 100).data, b"{}")
        real_directory = self.base / "real"
        real_directory.mkdir()
        write(real_directory / "inside.json", "{}")
        directory_link = self.base / "linked-directory"
        directory_link.symlink_to(real_directory, target_is_directory=True)
        self.assertEqual(bounded_read(directory_link / "inside.json", 100).data, b"{}")
        write(self.base / "large.json", "x" * 101)
        self.assertEqual(bounded_read(self.base / "large.json", 100).error, "oversized")
        self.assertEqual(
            bounded_read(target, 100, required_owner_uid=os.getuid() + 1).error,
            "insecure-owner",
        )
        target.chmod(0o664)
        self.assertEqual(
            bounded_read(
                target,
                100,
                required_owner_uid=os.getuid(),
                reject_group_or_world_writable=True,
            ).error,
            "insecure-mode",
        )
        target.chmod(0o666)
        self.assertEqual(
            bounded_read(
                target,
                100,
                required_owner_uid=os.getuid(),
                reject_group_or_world_writable=True,
            ).error,
            "insecure-mode",
        )
        document = {
            "schemaVersion": 1,
            "healthBasis": "configuration-only",
            "definitions": [{"value": "x" * 10000} for _ in range(100)],
        }
        self.assertLessEqual(len(bounded_json(document).encode()), MAX_OUTPUT_BYTES)

    def test_parsers_are_stdlib_bounded_and_jsonc_aware(self) -> None:
        parsed = parse_jsonc(b'{"url":"https://example.invalid//kept",/*drop*/"x":[1,],}')
        self.assertEqual(parsed["x"], [1])
        self.assertEqual(parsed["url"], "https://example.invalid//kept")
        self.assertEqual(parse_toml(b'[mcp_servers.a]\ncommand="x"\n')["mcp_servers"]["a"]["command"], "x")

    def test_deadline_is_bounded_and_safe(self) -> None:
        result = self.inventory(deadline_seconds=-1).scan()
        self.assertTrue(result["truncated"])
        self.assertIn({"code": "deadline", "sourceId": "inventory"}, result["warnings"])

    def test_cli_emits_only_json(self) -> None:
        stdout = io.StringIO()
        with mock.patch.dict(os.environ, {
            "HOME": str(self.home),
            "CODEX_HOME": str(self.home / ".codex"),
        }, clear=True), mock.patch("sys.stdout", stdout):
            code = main(["list", "--project", str(self.project), "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout.getvalue())["schemaVersion"], 1)


class ScopeLaneCase(InventoryCase):
    def test_scope_lanes_split_user_and_project(self) -> None:
        self.populate()
        user = self.inventory(scope="user").scan()["definitions"]
        project = self.inventory(scope="project").scan()["definitions"]
        both = self.inventory().scan()["definitions"]
        user_scopes = {row["scope"] for row in user}
        self.assertFalse({"project", "local"} & user_scopes, user_scopes)
        self.assertEqual({row["scope"] for row in project}, {"project", "local"})
        self.assertEqual(len(both), len(user) + len(project))
        self.assertEqual({row["id"] for row in both}, {row["id"] for row in user} | {row["id"] for row in project})
        from fileblade_inventory import WatchPlan
        with WatchPlan() as plan:
            watched = plan.finish(self.inventory(scope="project").scan())["watchPaths"]
        user_roots = tuple(str(self.home / part) for part in (".codex/plugins", ".copilot", ".gemini/extensions",
                                                              ".gemini/antigravity-cli", ".claude/plugins"))
        leaked = [str(path) for path in watched if str(path).startswith(user_roots)]
        self.assertFalse(leaked, leaked)


class ApprovalCase(InventoryCase):
    def test_project_mcpjson_approvals_merge_from_settings_files(self) -> None:
        self.populate()
        json_write(self.project / ".claude" / "settings.json", {"enabledMcpjsonServers": ["pi-high"]})
        json_write(self.project / ".claude" / "settings.local.json", {"disabledMcpjsonServers": ["project-only"]})
        rows = {row["name"]: row for row in self.inventory().scan()["definitions"]
                if row["scope"] == "project" and row["agent"] == "claude"}
        trusted = {name: row.get("trusted") for name, row in rows.items()}
        self.assertEqual(trusted.get("pi-high"), True, trusted)
        self.assertEqual(trusted.get("project-only"), False, trusted)


class QmlContractCase(unittest.TestCase):
    def test_satellite_lifecycle_and_shared_tree_contract(self) -> None:
        root = Path(__file__).parents[1]
        module = (root / "blades" / "Module.qml").read_text(encoding="utf-8")
        keys = (root / "components" / "RescanKeyContract.qml").read_text(encoding="utf-8")
        readme = (root / "docs" / "agent-written" / "README.md").read_text(encoding="utf-8")
        self.assertIn("property var context: null", module)
        for target in ("searchLoader", "treeLoader"):
            for option in ("caseSensitive", "regex"):
                self.assertIn(f'target: {target}.item; property: "{option}"; value: root.{option}', module)
        self.assertIn("function onOptionsToggled(nextCase, nextRegex)", module)
        self.assertIn("item.editPathFor = function(entry) { return root.absoluteSource(entry) }", module)
        self.assertIn("path: absoluteSource(entry), paths: []", module)
        self.assertIn("files.navigateToLocation(context.paths.parent(path)", module)
        self.assertIn("item.fileActionsFor = function(entry) { return false }", module)
        self.assertIn("inventory ? inventory.anchorPath", module)
        self.assertIn("readonly property var definitions: inventory ? inventory.items", module)
        for shared in ("busy", "truncated", "applying", "loadError", "applyError", "watchError"):
            self.assertIn(f"inventory.{shared}", module)
        for duplicate in ("boundedRows", "boundedMetrics", "scanProcess", "applyProcess", "refreshDebounce"):
            self.assertNotIn(duplicate, module)
        service = (root / "Service.qml").read_text()
        provider = (root / "Provider.qml").read_text()
        self.assertIn('context.ui.url("ArtifactInventory")', service)
        self.assertIn('itemsKey: "definitions", healthBasis: "configuration-only"', provider)
        self.assertIn('exactProject: false, scanArguments: ["--watch"]', provider)
        self.assertIn('observers: Qt.binding(function() { return provider.observers })', provider)
        self.assertIn("Component.onDestruction: if (attachedProvider) attachedProvider.detach(attachedContext)", module)
        self.assertIn("onInventoryActiveChanged: syncProvider()", module)
        manifest = json.loads((root / "manifest.json").read_text())
        helper = manifest["extensions"]["data-goblin.fileblade/helper"][0]
        self.assertEqual(helper["entry"], "bin/agent-mcpctl")
        self.assertEqual(helper["read"], ["list", "prepare-remove"])
        self.assertEqual(helper["write"], ["apply", "remove-prepared", "restore"])
        self.assertIn("if (!item.source) return item.path ? String(item.path) : \"\"", module)
        self.assertIn("root.absoluteSource(entry)].join(\" \")", module)
        self.assertNotIn("entry.source.path].join", module)
        self.assertNotIn("syntaxKeys", module)
        self.assertIn("context.contractVersion >= 1", module)
        self.assertNotIn("context.contractVersion === 1", module)
        self.assertIn("context.bladeOpen && !context.collapsed", module)
        self.assertEqual(module.count("active: root.inventoryActive"), 2)
        self.assertIn("wanted: root.inventoryActive && !!root.files", module)
        self.assertIn('setSource(root.context.ui.url("ArtifactBin"), { service: root.files })', module)
        self.assertIn('item.module = "mcp"\n      item.context = Qt.binding(function() { return root.context })', module)
        self.assertIn("item.helperRoute = Qt.binding(", module)
        self.assertIn("item.removalArguments = function(entry)", module)
        self.assertNotIn("binProcess", module)
        self.assertNotIn("binCallback", module)
        self.assertIn("binLoader.item.mergeRows(definitions, binned, function(entry) { return [entry.agent, entry.scope === \"plugin\" ? \"user\" : entry.scope] })", module)
        self.assertIn("position: Math.max(0, allRows().indexOf(entry))", module)
        self.assertIn('groups: [entry.agent, entry.scope === "plugin" ? "user" : entry.scope]', module)
        self.assertIn("metrics: entry.metrics", module)
        cli = (root / "agent_mcp" / "cli.py").read_text(encoding="utf-8")
        self.assertIn('add_argument("--record-id", required=True)', cli)
        self.assertIn('add_argument("--payload-stdin", action="store_true", required=True)', cli)
        self.assertNotIn('add_argument("--payload")', cli)
        self.assertIn('property: "tabIndex"', module)
        self.assertIn('property: "reservedLeft"', module)
        self.assertIn('property: "reservedRight"', module)
        self.assertIn("readonly property var view: viewLoader.item", module)
        self.assertIn('root.context.ui.url("PaneView")', module)
        self.assertIn("readonly property var metricOptions", module)
        self.assertIn(
            'context.metrics.options(["off", "agents", "status", { key: "transport", shortLabel: "TYPE" }, '
            '"updated", "created", "tokens", "characters", "words", "bytes", "summary"])',
            module,
        )
        self.assertNotIn('label: "Tokens', module)
        self.assertIn("function onAgentsAllRequested(entry, on) { root.toggleAllAgents(entry, on) }", module)
        self.assertIn('property: "defaultMetric"; value: "agents"', module)
        self.assertEqual(module.count('property: "view"; value: root.view'), 2)
        self.assertIn('property: "installedAgents"; value: root.installedAgents', module)
        self.assertIn("files && Array.isArray(files.installedAgents) ? files.installedAgents : []", module)
        self.assertIn("function specialMetricValue(entry, key)", module)
        self.assertIn("function appliedAgents(entry)", module)
        self.assertIn("item.appliedAgents = function", module)
        self.assertIn("function onFilterRequested()", module)
        self.assertIn("headerLoader.item.openFilter()", module)
        self.assertIn("function onAgentToggled(entry, agentId, on)", module)
        self.assertIn("function onAgentsAllRequested(entry, on)", module)
        self.assertIn('["--project", projectPath, "--id", String(entry.id)]', module)
        self.assertIn('inventory.mutate("apply", command)', module)
        self.assertIn('command.push("--state", state, "--json")', module)
        self.assertIn('readonly property string status: applying ? "Applying…" : (applyMessage !== "" ? applyMessage : statusText)', module)
        self.assertIn('property: "status"; value: root.status', module)
        self.assertNotIn("headerStatus", module)
        self.assertIn('"Applying…"', module)
        self.assertIn("applyMessage !== \"\" ? applyMessage : statusText", module)
        self.assertIn('return id !== own', module)
        for stale in ("metricKey", "setMetric(", "restoreMetric", "onMetricChosen", 'property: "metricOptions"', 'context.state'):
            self.assertNotIn(stale, module)
        self.assertIn("item.specialMetricValue = function", module)
        self.assertNotIn("showDescriptions", module)
        self.assertNotIn("detailToggle", module)
        self.assertNotIn('"DESC"', module)
        self.assertIn("visible: !!headerLoader.item && headerLoader.item.visible", module)
        for callback in ("groupsFor", "leafLabel", "leafDetail", "searchText"):
            self.assertIn(f"item.{callback} = function", module)
            self.assertNotIn(f'property: "{callback}"; value: function', module)
        self.assertNotIn("item.leafBadge", module)
        self.assertIn("function onFocusNextRequested()", module)
        self.assertIn("function onFocusPreviousRequested()", module)
        self.assertIn("Components.RescanKeyContract", module)
        self.assertIn('item.source.redacted !== false', module)
        self.assertIn('Quickshell.env("CODEX_HOME")', module)
        self.assertIn('if (loadError) return loadError', module)
        self.assertNotIn("runAction(", module)
        self.assertNotIn("Shortcut {", module)
        self.assertNotIn("Keys.priority", module)
        self.assertEqual(module.count("event.accepted = true"), 1)
        self.assertIn("key === Qt.Key_R && modifiers === Qt.ShiftModifier", keys)
        self.assertIn("$CODEX_HOME/*.config.toml", readme)
        self.assertIn("agent-mcpctl apply --project <dir> --id <definition id> --agent <agent id> [--agent <id> ...] --state on|off --json", readme)
        for target in ("~/.claude.json", "~/.codex/config.toml", "~/.config/opencode/opencode.json",
                       "~/.pi/agent/mcp.json", "~/.copilot/mcp-config.json", "~/.gemini/config/mcp_config.json"):
            self.assertIn(target, readme)
        self.assertNotIn("Writes outside the checkout: none", readme)
        self.assertNotIn("Agent configuration is never changed", readme)
        self.assertIn("https://developers.openai.com/codex/config-reference/", readme)
        self.assertNotIn("/etc/opencode", readme)
        self.assertNotIn("·", module + readme)


if __name__ == "__main__":
    unittest.main()
