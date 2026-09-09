import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
KIND = json.loads((ROOT / "manifest.json").read_text())["id"].removeprefix("data-goblin.fileblade-")
PROVIDER = "data-goblin.fileblade-" + KIND


class CoreBinLifecycle(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="fileblade-bin-lifecycle-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        self.project = self.home / "project"
        self.project.mkdir(parents=True)
        self.binary = os.environ.get("FILEBLADE_BINARY", str(ROOT.parent / "fileblade/fileblade"))
        self.env = dict(os.environ, HOME=str(self.home), XDG_CONFIG_HOME=str(self.home / ".config"),
                        XDG_STATE_HOME=str(self.home / ".local/state"), XDG_DATA_HOME=str(self.home / ".local/share"),
                        CODEX_HOME=str(self.home / ".codex"), CLAUDE_CONFIG_DIR=str(self.home / ".claude"),
                        FILEBLADE_BINARY=self.binary, PYTHONDONTWRITEBYTECODE="1")
        scripts = self.home / "bin"
        scripts.mkdir()
        self.enabled = self.home / "enabled.json"
        self.set_enabled(True)
        command = scripts / "omarchy"
        command.write_text('#!/usr/bin/python3\nimport os, pathlib, sys\n'
                           'assert sys.argv[1:] == ["plugin", "list", "--json"]\n'
                           'print((pathlib.Path(os.environ["HOME"])/"enabled.json").read_text())\n')
        command.chmod(0o700)
        self.env["PATH"] = str(scripts) + os.pathsep + os.environ["PATH"]
        plugins = self.home / ".config/omarchy/plugins"
        plugins.mkdir(parents=True)
        (plugins / PROVIDER).symlink_to(ROOT, target_is_directory=True)
        self.source = self.project / (".mcp.json" if KIND == "mcp" else ".claude/settings.json")
        self.source.parent.mkdir(exist_ok=True)
        self.original = ({"mcpServers": {"fixture": {"command": "printf", "args": ["fixture"]}}}
                         if KIND == "mcp" else {"hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "printf fixture"}]}]}})
        manifest = json.loads((ROOT / "manifest.json").read_text())
        helper = manifest["extensions"]["data-goblin.fileblade/helper"][0]["id"]
        self.route = json.dumps({"provider": PROVIDER, "directory": str(ROOT), "helper": helper})
        self.store = self.home / (".local/state/fileblade/" + KIND + "-recovery")
        self.reset_source()

    def set_enabled(self, enabled):
        self.enabled.write_text(json.dumps([{"id": PROVIDER, "enabled": enabled}]))

    def reset_source(self):
        self.source.write_text(json.dumps(self.original))

    def backend(self, *args):
        result = subprocess.run([self.binary, "_backend", *args], env=self.env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
        if not result.stdout:
            return {"ok": False, "error": result.stderr.decode()}
        return json.loads(result.stdout)

    def remove(self):
        result = subprocess.run([str(ROOT / ("bin/agent-" + KIND + "ctl")), "list", "--project", str(self.project), "--json"],
                                env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20, check=True)
        document = json.loads(result.stdout)
        rows = document["definitions"] if KIND == "mcp" else document["items"]
        row = rows[0]
        response = self.backend("bin-remove", "--module", KIND, "--item", json.dumps({"id": row["id"], "name": "Fixture"}),
                                "--helper-route", self.route, "--arguments", json.dumps(["--project", str(self.project), "--id", row["id"], "--json"]))
        self.assertTrue(response.get("ok"), response)
        return response["entry"]

    def test_remove_and_purge_past_the_store_capacity_leaves_no_hidden_recovery(self):
        for _ in range(70):
            entry = self.remove()
            self.assertEqual(len(list(self.store.glob("*.json"))), 1)
            response = self.backend("bin-purge", "--module", KIND, "--id", entry)
            self.assertTrue(response.get("ok"), response)
            self.assertEqual(list(self.store.glob("*.json")), [])
            self.assertEqual(self.backend("bin-list", "--module", KIND)["items"], [])
            self.reset_source()

    def test_disabled_restore_preserves_both_records_until_explicit_reenable(self):
        entry = self.remove()
        removed = self.source.read_bytes()
        self.set_enabled(False)
        response = self.backend("bin-restore", "--module", KIND, "--id", entry)
        self.assertFalse(response.get("ok"), response)
        self.assertEqual(self.source.read_bytes(), removed)
        self.assertEqual(len(list(self.store.glob("*.json"))), 1)
        self.assertEqual(len(self.backend("bin-list", "--module", KIND)["items"]), 1)
        self.set_enabled(True)
        response = self.backend("bin-restore", "--module", KIND, "--id", entry)
        self.assertTrue(response.get("ok"), response)
        self.assertEqual(json.loads(self.source.read_text()), self.original)
        self.assertEqual(list(self.store.glob("*.json")), [])


if __name__ == "__main__":
    unittest.main()
