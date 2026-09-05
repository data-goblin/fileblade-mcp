import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from agent_mcp import Inventory
from fileblade_paths import path_text

ROOT = Path(__file__).resolve().parents[1]


class NativePaths(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.home = self.base / "home"
        self.home.mkdir()
        self.project = self.base / os.fsdecode(b"repo-\xff")
        self.project.mkdir()
        self.source = self.project / ".mcp.json"
        self.original = {"mcpServers": {"native-test": {"command": "printf", "args": ["TEST_NOT_EXECUTED"], "env": {"TOKEN": "SECRET_NOT_EMITTED"}}}}
        self.source.write_text(json.dumps(self.original))
        self.neighbor = self.base / "repo-\ufffd"
        self.neighbor.mkdir()
        (self.neighbor / ".mcp.json").write_text("neighbor")
        self.env = {"PATH": os.environ["PATH"], "HOME": str(self.home), "PYTHONDONTWRITEBYTECODE": "1",
                    "XDG_DATA_HOME": str(self.base / "data"), "XDG_STATE_HOME": str(self.base / "state")}

    def run_helper(self, command, *arguments, payload=None):
        result = subprocess.run([str(ROOT / "bin/agent-mcpctl"), command, "--json", "--project", path_text(str(self.project)), *arguments],
                                input=None if payload is None else json.dumps(payload).encode() + b"\n",
                                env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        return json.loads(result.stdout.decode("utf-8"))

    def core(self, command, *arguments):
        raw = subprocess.check_output([str(ROOT.parent / "fileblade/fileblade"), "_backend", command, *arguments], env=self.env)
        return json.loads(raw)

    def row(self):
        return next(row for row in self.run_helper("list")["definitions"] if row["agentId"] == "claude-code" and row["name"] == "native-test")

    def test_native_project_still_obeys_redaction(self):
        self.assertEqual(Inventory(path_text(str(self.project)), home=self.home, environment={}).project, self.project)
        document = self.run_helper("list")
        row = self.row()
        self.assertEqual(row["source"]["path"], "<project>/.mcp.json")
        self.assertFalse(row["source"]["redacted"])
        self.assertNotIn("SECRET_NOT_EMITTED", json.dumps(document))
        self.assertNotIn("TEST_NOT_EXECUTED", json.dumps(document))

    def test_native_source_undo_through_core_bin(self):
        row = self.row()
        removed = self.run_helper("remove", "--id", row["id"])
        self.assertTrue(removed["ok"], removed)
        self.assertEqual(removed["payload"]["path"], path_text(str(self.source)))
        item = {"id": row["id"], "name": row["name"], "path": path_text(str(self.source)), "paths": [], "payload": removed["payload"]}
        stored = self.core("bin-put", "--module", "mcp", "--item", json.dumps(item))
        self.assertTrue(stored["ok"], stored)
        record = self.core("bin-restore", "--module", "mcp", "--id", stored["entry"])
        self.assertTrue(record["ok"], record)
        restored = self.run_helper("restore", "--record-id", stored["entry"], "--payload-stdin", payload=record["payload"])
        self.assertTrue(restored["ok"], restored)
        self.assertEqual(json.loads(self.source.read_text()), self.original)
        self.assertEqual((self.neighbor / ".mcp.json").read_text(), "neighbor")

    def test_unrepresentable_undo_refuses_before_removal(self):
        invalid = {"mcpServers": {"native-test": {"command": "printf", "env": {"TOKEN": "\udcff"}}}}
        self.source.write_text(json.dumps(invalid))
        before = self.source.read_bytes()
        removed = self.run_helper("remove", "--id", self.row()["id"])
        self.assertFalse(removed["ok"], removed)
        self.assertEqual(self.source.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
