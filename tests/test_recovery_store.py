import importlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
KIND = json.loads((ROOT / "manifest.json").read_text())["id"].removeprefix("data-goblin.fileblade-")
recovery = importlib.import_module("agent_" + KIND + ".recovery")


class RecoveryLifecycle(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = recovery.RecoveryStore(Path(self.temporary.name) / "store")

    def test_preparation_reuses_one_transaction_but_distinct_removals_keep_distinct_records(self):
        first = self.store.write({"source":"fixture"}, "row", {}, "a" * 32)
        again = self.store.write({"source":"fixture"}, "row", {}, "a" * 32)
        second = self.store.write({"source":"fixture"}, "row", {}, "b" * 32)
        self.assertEqual(first, "a" * 32)
        self.assertEqual(again, first)
        self.assertNotEqual(first, second)
        self.assertTrue(self.store.discard_payload(first)["ok"])
        self.assertIsNotNone(self.store.read(second))
        self.assertEqual(self.store.live_records(), 1)

    def test_legacy_inventory_exposes_ids_without_payloads_or_changes(self):
        record = self.store.write({"secret": "ordinary fixture token"}, "private definition", {"home": "/private"})
        before = self.store.record_path(record).read_bytes()
        document = self.store.inventory()
        self.assertTrue(document["ok"])
        self.assertEqual(document["records"][0]["recordId"], record)
        self.assertFalse(document["records"][0]["trackedTransaction"])
        self.assertNotIn("ordinary fixture token", json.dumps(document))
        self.assertNotIn("/private", json.dumps(document))
        self.assertEqual(self.store.record_path(record).read_bytes(), before)
        self.assertTrue(self.store.discard_payload(record)["ok"])
        self.assertEqual(self.store.inventory()["records"], [])

    def test_private_record_and_directory_modes_are_required_for_cleanup(self):
        record = self.store.write({"source":"fixture"}, "row", {})
        path = self.store.record_path(record)
        path.chmod(0o644)
        self.assertIsNone(self.store.read(record))
        self.assertFalse(self.store.discard_payload(record)["ok"])
        self.assertTrue(path.exists())
        path.chmod(0o600)
        self.store.directory.chmod(0o755)
        self.assertFalse(self.store.discard_payload(record)["ok"])
        self.assertTrue(path.exists())
        self.store.directory.chmod(0o700)
        self.assertTrue(self.store.discard_payload(record)["ok"])

    def test_limits_refuse_new_records_without_evicting_pending_undo(self):
        first = self.store.write({"source":"first"}, "row", {})
        with patch.object(recovery, "MAX_RECORDS", 1):
            with self.assertRaises(recovery.RecoveryFull):
                self.store.write({"source":"second"}, "row", {})
        self.assertIsNotNone(self.store.read(first))
        with patch.object(recovery, "MAX_SCANNED_RECORDS", 1), patch.object(self.store, "_read") as reading:
            with self.assertRaises(recovery.RecoveryFull):
                self.store.records()
            reading.assert_not_called()
        with patch.object(recovery, "MAX_STORE_BYTES", 10):
            with self.assertRaises(OSError):
                self.store.records()
        self.assertIsNotNone(self.store.read(first))

    def test_new_records_charge_existing_bytes_and_interrupted_staging(self):
        first = self.store.write({"source":"first"}, "row", {})
        path = self.store.record_path(first)
        document = json.loads(path.read_bytes())
        path.write_text(json.dumps(document, indent=8))
        staged = self.store.directory / "interrupted.staged"
        staged.write_bytes(b"x" * 32)
        staged.chmod(0o600)
        with patch.object(recovery, "MAX_STORE_BYTES", path.stat().st_size + 40):
            with self.assertRaises(recovery.RecoveryFull):
                self.store.write({"source":"second"}, "row", {})
        self.assertIsNotNone(self.store.read(first))

    def test_unused_records_stay_until_explicit_discard_and_used_records_expire(self):
        first = self.store.write({"source":"first"}, "row", {})
        second = self.store.write({"source":"second"}, "row", {})
        self.store.mark_restored(second)
        with patch.object(recovery.time, "time", return_value=recovery.time.time() + 4000 * 86400):
            self.store.expired()
        self.assertIsNotNone(self.store.read(first))
        self.assertIsNone(self.store.read(second))
        self.assertTrue(self.store.discard_payload(first)["ok"])
        self.assertTrue(self.store.discard_payload(first)["ok"])


if __name__ == "__main__":
    unittest.main()
