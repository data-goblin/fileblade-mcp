import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
HOST = "data-goblin.fileblade"


class HostStatus(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.env = dict(os.environ, PATH=str(self.base), HOST_FIXTURE=str(self.base), PYTHONDONTWRITEBYTECODE="1")
        self.rows = [{"id": HOST, "name": "FileBlade", "enabled": True},
                     {"id": HOST + "-skills", "name": "Skills", "enabled": True}]
        self.status = {"open": True, "rootPath": "/fixture"}
        script = "#!" + sys.executable + "\nimport os, pathlib, sys\nroot = pathlib.Path(os.environ['HOST_FIXTURE'])\n"
        self.executable("omarchy", script + "assert sys.argv[1:] == ['plugin', 'list', '--json']\nprint((root/'list.json').read_text())\n")
        self.executable("omarchy-shell", script + "assert sys.argv[1:] == ['data-goblin.fileblade', 'status']\nprint((root/'status.json').read_text())\n")

    def executable(self, name, text):
        path = self.base / name
        path.write_text(text)
        path.chmod(0o700)

    def check(self):
        (self.base / "list.json").write_text(json.dumps(self.rows))
        (self.base / "status.json").write_text(json.dumps(self.status))
        result = subprocess.run([sys.executable, str(ROOT / "bin/fileblade-host-status")], env=self.env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=6, check=True)
        return json.loads(result.stdout)

    def test_ready_uses_public_commands_and_returns_only_companions(self):
        result = self.check()
        self.assertEqual(result["state"], "ready")
        self.assertEqual(result["plugins"], [self.rows[1]])

    def test_missing_disabled_and_starting_are_distinct(self):
        self.rows[0]["enabled"] = False
        self.assertEqual(self.check()["state"], "disabled")
        self.rows[0]["enabled"] = True
        self.status = {"error": "not ready"}
        self.assertEqual(self.check()["state"], "starting")
        self.rows = self.rows[1:]
        self.assertEqual(self.check()["state"], "missing")

    def test_failed_listing_is_unknown(self):
        self.executable("omarchy", "#!" + sys.executable + "\nraise SystemExit(1)\n")
        self.assertEqual(self.check()["state"], "unknown")

    def test_missing_command_is_unknown(self):
        (self.base / "omarchy").unlink()
        self.assertEqual(self.check()["state"], "unknown")

    def test_ambiguous_or_malformed_authority_is_unknown(self):
        for rows in ([self.rows[0], self.rows[0]], [{"id": HOST, "enabled": "true"}], {}, [None]):
            self.rows = rows
            self.assertEqual(self.check()["state"], "unknown")

    def test_listing_limits_and_deadline(self):
        self.rows = [{"id": "test.p" + str(i), "enabled": False} for i in range(513)]
        self.assertEqual(self.check()["state"], "unknown")
        self.executable("omarchy", "#!" + sys.executable + "\nprint('x' * 131073)\n")
        self.assertEqual(self.check()["state"], "unknown")
        self.executable("omarchy", "#!" + sys.executable + "\nimport time\ntime.sleep(10)\n")
        started = time.monotonic()
        self.assertEqual(self.check()["state"], "unknown")
        self.assertLess(time.monotonic() - started, 4)


if __name__ == "__main__":
    unittest.main()
