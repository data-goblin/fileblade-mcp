import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from agent_mcp import Inventory
from agent_mcp.safeio import Deadline, bounded_directories
from fileblade_inventory import WatchPlan
from fileblade_paths import path_text

ROOT = Path(__file__).resolve().parents[1]


class McpWatchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.home, self.project = self.base / "home", self.base / "project"
        self.home.mkdir(); self.project.mkdir()

    def scan(self):
        with WatchPlan() as plan:
            document = plan.finish(Inventory(self.project, home=self.home, environment={},
                                            etc_root=self.base / "etc").scan())
        return document, plan.paths

    def test_missing_sources_and_new_nested_plugins_are_watched(self):
        _, paths = self.scan()
        self.assertIn(self.project, paths)
        self.assertIn(self.home, paths)
        plugin = self.home / ".codex" / "plugins" / "cache" / "market" / "tool" / "1.0"
        plugin.mkdir(parents=True)
        (plugin / ".mcp.json").write_text('{"mcpServers":{"tool":{"command":"private-command"}}}')
        document, paths = self.scan()
        self.assertIn(plugin, paths)
        self.assertIn(plugin.parent, paths)
        self.assertNotIn("private-command", json.dumps(document))

    def test_source_symlink_target_and_logical_parent_are_both_watched(self):
        source = self.project / ".mcp.json"
        target = self.base / "vault" / "config.json"
        target.parent.mkdir()
        target.write_text('{"mcpServers":{"tool":{"command":"private-command"}}}')
        source.symlink_to(target)
        document, paths = self.scan()
        self.assertIn(source.parent, paths)
        self.assertIn(target.parent, paths)
        self.assertTrue(document["definitions"])
        self.assertNotIn("private-command", json.dumps(document))

    def test_watch_paths_are_private_opt_in_and_native_byte_faithful(self):
        self.project = self.base / os.fsdecode(b"SECRET_PROJECT-\xff")
        self.project.mkdir()
        arguments = [str(ROOT / "bin/agent-mcpctl"), "list", "--project", path_text(str(self.project)), "--json"]
        environment = {"PATH": os.environ["PATH"], "HOME": str(self.home)}
        plain = subprocess.check_output(arguments, env=environment)
        self.assertNotIn("watchPaths", json.loads(plain))
        self.assertNotIn(b"SECRET_PROJECT", plain)
        watched = json.loads(subprocess.check_output(arguments + ["--watch"], env=environment))
        self.assertTrue(watched["ok"])
        self.assertIn(path_text(str(self.project)), watched["watchPaths"])
        self.assertNotIn("\udcff", json.dumps(watched, ensure_ascii=False))
        self.assertLessEqual(len(watched["watchPaths"]), 512)

    def test_directory_input_bound_counts_nonmatching_entries(self):
        class Entry:
            name = "file"
            def is_dir(self, **_): return False
        class Scan:
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def __iter__(self):
                for _ in range(5): yield Entry()
                raise AssertionError("directory scan read past its entry bound")
        deadline = Deadline(2)
        with patch("agent_mcp.safeio.os.scandir", return_value=Scan()):
            self.assertEqual(bounded_directories(self.project, 4, deadline), [])
        self.assertTrue(deadline.truncated)

    def test_declared_helper_runs_through_native_backend(self):
        from fileblade_paths import __file__ as shared_path
        binary = Path(shared_path).resolve().parents[1] / "fileblade-bin"
        arguments = ["--project", str(self.project), "--json", "--watch"]
        document = json.loads(subprocess.check_output([
            str(binary), "_backend", "helper-read", "--provider", "data-goblin.fileblade-mcp",
            "--plugin-dir", str(ROOT), "--helper", "inventory", "--method", "list", "--arguments", json.dumps(arguments)
        ], env={"PATH": os.environ["PATH"], "HOME": str(self.home), "XDG_STATE_HOME": str(self.base / "state")}))
        self.assertTrue(document["ok"])
        self.assertEqual(document["healthBasis"], "configuration-only")
        self.assertIn(str(self.project), document["watchPaths"])
        self.assertEqual(document["definitions"], [])


if __name__ == "__main__":
    unittest.main()
