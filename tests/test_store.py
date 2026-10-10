from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from nexus_workbench import ActionCell, ActionMode, ArtifactRecord, Candidate, JsonWorkbenchStore, WorkbenchNotebook


class StoreTests(unittest.TestCase):
    def make_notebook(self) -> WorkbenchNotebook:
        return WorkbenchNotebook(
            workbench_session_id="wb-resume-1",
            task_id="task-1",
            operation_id="op-1",
            attempt_id="attempt-1",
            target_identity="git-tree:abc",
            goal="Resume independently",
            current_objective="inspect state",
            next_probe="load notebook",
            observation_refs=("O1",),
        )

    def test_notebook_persists_without_transcript_or_provider_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonWorkbenchStore(Path(tmp))
            original = self.make_notebook()
            path = store.save_notebook(original)
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("transcript", text.lower())
            self.assertNotIn("provider_session", text.lower())
            self.assertEqual(store.load_notebook(original.workbench_session_id), original)

    def test_store_detects_disk_tamper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonWorkbenchStore(Path(tmp))
            original = self.make_notebook()
            path = store.save_notebook(original)
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["goal"] = "tampered"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                store.load_notebook(original.workbench_session_id)

    def test_store_rejects_path_escape_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonWorkbenchStore(Path(tmp))
            with self.assertRaisesRegex(ValueError, "unsafe path"):
                store.load_notebook("../escape")

    def test_store_rejects_symlinked_artifacts_directory_outside_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "state"
            outside = Path(tmp) / "outside"
            root.mkdir()
            outside.mkdir()
            (root / "artifacts").symlink_to(outside, target_is_directory=True)
            store = JsonWorkbenchStore(root)
            artifact = ArtifactRecord("wb-1", "A1", "application/json", {"ok": True})
            with self.assertRaisesRegex(ValueError, "escapes Workbench store"):
                store.save_artifact(artifact)
            self.assertEqual(list(outside.iterdir()), [])

    def test_store_rejects_symlinked_locks_directory_outside_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "state"
            outside = Path(tmp) / "outside"
            root.mkdir()
            outside.mkdir()
            (root / "locks").symlink_to(outside, target_is_directory=True)
            store = JsonWorkbenchStore(root)
            with self.assertRaisesRegex(ValueError, "escapes Workbench store"):
                store.save_notebook(self.make_notebook())
            self.assertEqual(list(outside.iterdir()), [])

    def test_action_and_candidate_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonWorkbenchStore(Path(tmp))
            action = ActionCell("wb-1", "S1", 1, "inspect", ActionMode.READ_ONLY_PROBE)
            candidate = Candidate("wb-1", "task-1", "git-tree:abc", "bounded diagnosis")
            store.save_action(action)
            store.save_candidate(candidate)
            self.assertEqual(store.load_action("wb-1", "S1"), action)
            self.assertEqual(store.load_candidate("wb-1"), candidate)


if __name__ == "__main__":
    unittest.main()
