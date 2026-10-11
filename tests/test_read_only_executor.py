from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest import mock

from nexus_workbench import ActionCell, ActionMode, ObservationOutcome
from nexus_workbench import read_only_executor
from nexus_workbench.read_only_executor import LocalReadOnlyExecutor, ReadOnlyPolicyError


def git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return proc.stdout


class RepoFixture:
    def __init__(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.target = self.root / "target"
        self.scratch = self.root / "scratch"
        self.outside = self.root / "outside.txt"
        self.target.mkdir()
        self.scratch.mkdir()
        self.outside.write_text("outside secret\n", encoding="utf-8")

        git(self.target, "init")
        git(self.target, "config", "user.email", "g2@example.invalid")
        git(self.target, "config", "user.name", "G2 Fixture")

        (self.target / "README.md").write_text("alpha needle omega\n", encoding="utf-8")
        (self.target / ".gitignore").write_text("ignored.log\n", encoding="utf-8")
        tests = self.target / "tests"
        tests.mkdir()
        (tests / "test_safe.py").write_text(
            "import unittest\n"
            "raise RuntimeError('test discovery must not import this module')\n\n"
            "def test_example():\n"
            "    pass\n\n"
            "class OddNamedSuite(unittest.TestCase):\n"
            "    def test_class_case(self):\n"
            "        pass\n",
            encoding="utf-8",
        )
        (self.target / "escape-link").symlink_to(self.outside)
        git(self.target, "add", ".")
        git(self.target, "commit", "-m", "fixture")

    def close(self) -> None:
        self.temp.cleanup()


def action(
    step: str,
    op: str,
    *,
    mode: ActionMode = ActionMode.READ_ONLY_PROBE,
    **payload: object,
) -> ActionCell:
    return ActionCell(
        "wb-g2",
        step,
        1,
        f"exercise {op}",
        mode,
        (op,),
        code_or_action=json.dumps({"op": op, **payload}, sort_keys=True),
    )


class ReadOnlyExecutorCanaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = RepoFixture()
        self.executor = LocalReadOnlyExecutor.bind(
            self.fixture.target,
            self.fixture.scratch,
        )
        self.baseline = self.executor.snapshot()

    def tearDown(self) -> None:
        self.fixture.close()

    def assert_target_unchanged(self, observation) -> None:
        self.assertEqual(observation.changed_target_paths, ())
        self.assertEqual(
            observation.physical_readback["before"]["identity"],
            self.baseline.identity,
        )
        self.assertEqual(
            observation.physical_readback["after"]["identity"],
            self.baseline.identity,
        )
        self.assertEqual(self.executor.snapshot().identity, self.baseline.identity)

    def test_allowed_repository_probes_preserve_physical_target(self) -> None:
        probes = [
            action("S1", "repo.read", path="README.md"),
            action("S2", "repo.search", query="needle", path="."),
            action("S3", "git.status"),
            action("S4", "git.diff"),
            action("S5", "test.discover", path="tests"),
        ]

        observations = [self.executor.execute(item) for item in probes]
        self.assertTrue(all(obs.outcome == ObservationOutcome.SUCCEEDED for obs in observations))
        for observation in observations:
            self.assert_target_unchanged(observation)

        self.assertEqual(
            observations[0].physical_readback["result"]["text"],
            "alpha needle omega\n",
        )
        self.assertEqual(
            observations[1].physical_readback["result"]["matches"][0]["path"],
            "README.md",
        )
        discovered = observations[4].physical_readback["result"]["tests"]
        self.assertIn("tests/test_safe.py::test_example", discovered)
        self.assertIn(
            "tests/test_safe.py::OddNamedSuite::test_class_case",
            discovered,
        )

    def test_artifact_write_is_confined_to_disjoint_scratch(self) -> None:
        observation = self.executor.execute(
            action(
                "S6",
                "artifact.write",
                mode=ActionMode.PURE_COMPUTE,
                path="notes/result.txt",
                content="bounded artifact\n",
            )
        )
        self.assertEqual(observation.outcome, ObservationOutcome.SUCCEEDED)
        self.assert_target_unchanged(observation)
        self.assertEqual(observation.artifact_refs, ("scratch:notes/result.txt",))
        self.assertEqual(
            (self.fixture.scratch / "notes" / "result.txt").read_text(encoding="utf-8"),
            "bounded artifact\n",
        )

    def test_artifact_parent_and_symlink_escape_are_blocked(self) -> None:
        outside_artifact = self.fixture.root / "escaped-artifact.txt"
        parent_escape = self.executor.execute(
            action(
                "S6a",
                "artifact.write",
                mode=ActionMode.PURE_COMPUTE,
                path="../escaped-artifact.txt",
                content="escape",
            )
        )

        scratch_link = self.fixture.scratch / "target-link"
        scratch_link.symlink_to(self.fixture.target)
        symlink_escape = self.executor.execute(
            action(
                "S6b",
                "artifact.write",
                mode=ActionMode.PURE_COMPUTE,
                path="target-link/injected.txt",
                content="escape",
            )
        )

        for observation in (parent_escape, symlink_escape):
            self.assertEqual(observation.outcome, ObservationOutcome.BLOCKED)
            self.assert_target_unchanged(observation)
        self.assertFalse(outside_artifact.exists())
        self.assertFalse((self.fixture.target / "injected.txt").exists())

    def test_effectful_source_write_request_is_blocked_without_delta(self) -> None:
        observation = self.executor.execute(
            action(
                "S7",
                "repo.write",
                mode=ActionMode.EFFECTFUL,
                path="README.md",
                content="mutate",
            )
        )
        self.assertEqual(observation.outcome, ObservationOutcome.BLOCKED)
        self.assertIn("rejects EFFECTFUL", observation.result_summary)
        self.assert_target_unchanged(observation)
        self.assertEqual(
            (self.fixture.target / "README.md").read_text(encoding="utf-8"),
            "alpha needle omega\n",
        )

    def test_parent_and_absolute_path_escape_are_blocked(self) -> None:
        parent_escape = self.executor.execute(
            action("S8", "repo.read", path="../outside.txt")
        )
        absolute_escape = self.executor.execute(
            action("S9", "repo.read", path=str(self.fixture.outside))
        )
        for observation in (parent_escape, absolute_escape):
            self.assertEqual(observation.outcome, ObservationOutcome.BLOCKED)
            self.assertIn("path escape", observation.result_summary)
            self.assert_target_unchanged(observation)

    def test_symlink_escape_is_blocked(self) -> None:
        observation = self.executor.execute(
            action("S10", "repo.read", path="escape-link")
        )
        self.assertEqual(observation.outcome, ObservationOutcome.BLOCKED)
        self.assertIn("symlink/path escape", observation.result_summary)
        self.assert_target_unchanged(observation)

    def test_dirty_target_is_blocked_before_probe(self) -> None:
        (self.fixture.target / "README.md").write_text("dirty change\n", encoding="utf-8")
        drifted = self.executor.snapshot()
        self.assertNotEqual(drifted.identity, self.baseline.identity)

        observation = self.executor.execute(
            action("S11", "repo.read", path="README.md")
        )
        self.assertEqual(observation.outcome, ObservationOutcome.BLOCKED)
        self.assertIn("drifted before action", observation.result_summary)
        self.assertEqual(
            observation.physical_readback["before"]["identity"],
            drifted.identity,
        )

    def test_moved_clean_head_is_blocked_before_probe(self) -> None:
        (self.fixture.target / "README.md").write_text("new committed head\n", encoding="utf-8")
        git(self.fixture.target, "add", "README.md")
        git(self.fixture.target, "commit", "-m", "move head")
        moved = self.executor.snapshot()
        self.assertNotEqual(moved.head, self.baseline.head)
        self.assertEqual(moved.git_status, "")

        observation = self.executor.execute(
            action("S12", "repo.read", path="README.md")
        )
        self.assertEqual(observation.outcome, ObservationOutcome.BLOCKED)
        self.assertIn("drifted before action", observation.result_summary)

    def test_ignored_file_delta_is_detected_by_physical_manifest(self) -> None:
        (self.fixture.target / "ignored.log").write_text("ignored mutation\n", encoding="utf-8")
        drifted = self.executor.snapshot()
        self.assertNotEqual(drifted.manifest_hash, self.baseline.manifest_hash)

        observation = self.executor.execute(action("S13", "git.status"))
        self.assertEqual(observation.outcome, ObservationOutcome.BLOCKED)
        self.assertIn("drifted before action", observation.result_summary)

    def test_capability_substitution_and_unknown_op_fail_closed(self) -> None:
        wrong_capability = ActionCell(
            "wb-g2",
            "S14",
            1,
            "mismatch capability",
            ActionMode.READ_ONLY_PROBE,
            ("git.status",),
            code_or_action=json.dumps({"op": "repo.read", "path": "README.md"}),
        )
        unknown_op = action("S15", "shell.exec", command="touch README.md")
        for item in (wrong_capability, unknown_op):
            observation = self.executor.execute(item)
            self.assertEqual(observation.outcome, ObservationOutcome.BLOCKED)
            self.assert_target_unchanged(observation)

    def test_post_action_physical_mutation_is_detected(self) -> None:
        executor = self.executor

        original_dispatch = executor._dispatch

        def mutating_dispatch(action_cell, op, payload):
            result = original_dispatch(action_cell, op, payload)
            (self.fixture.target / "README.md").write_text("unexpected mutation\n", encoding="utf-8")
            return result

        executor._dispatch = mutating_dispatch  # type: ignore[method-assign]
        observation = executor.execute(action("S16", "repo.read", path="README.md"))
        self.assertEqual(observation.outcome, ObservationOutcome.FAILED)
        self.assertIn("mutation", observation.result_summary)
        self.assertIn("README.md", observation.changed_target_paths)

    def test_scratch_must_be_disjoint_from_target_without_creating_target_state(self) -> None:
        nested = self.fixture.target / "scratch"
        before = self.executor.snapshot()
        with self.assertRaisesRegex(ReadOnlyPolicyError, "disjoint"):
            LocalReadOnlyExecutor.bind(self.fixture.target, nested)
        self.assertFalse(nested.exists())
        self.assertEqual(self.executor.snapshot().identity, before.identity)

    def test_direct_construction_bypassing_bind_is_refused(self) -> None:
        # The constructor skips bind()'s exact-worktree, clean-target and
        # disjoint-scratch checks, so it must not yield an executor.
        with self.assertRaisesRegex(ReadOnlyPolicyError, "bind"):
            LocalReadOnlyExecutor(self.fixture.target, self.fixture.scratch, self.baseline)

    def test_identity_is_the_same_for_every_spelling_of_one_target(self) -> None:
        link = self.fixture.root / "target-link"
        link.symlink_to(self.fixture.target, target_is_directory=True)
        via_link = LocalReadOnlyExecutor.bind(link, self.fixture.root / "scratch-via-link")
        self.assertEqual(via_link.identity, self.executor.identity)
        self.assertEqual(via_link.target_identity, self.executor.target_identity)


class ManifestLimitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = RepoFixture()

    def tearDown(self) -> None:
        self.fixture.close()

    def test_fixture_manifest_stays_under_documented_limits(self) -> None:
        started = time.monotonic()
        entries = read_only_executor._manifest(self.fixture.target)
        elapsed = time.monotonic() - started
        file_bytes = sum(int(value.split(":")[2]) for value in entries.values() if value.startswith("F:"))
        self.assertEqual(len(entries), 5)
        self.assertLess(len(entries), read_only_executor._MAX_MANIFEST_ENTRIES)
        self.assertLess(file_bytes, read_only_executor._MAX_MANIFEST_BYTES)
        # Generous ceiling: a five-entry fixture must not take seconds.
        self.assertLess(elapsed, 2.0)

    def test_bind_refuses_target_over_entry_limit(self) -> None:
        with mock.patch.object(read_only_executor, "_MAX_MANIFEST_ENTRIES", 3):
            with self.assertRaisesRegex(ReadOnlyPolicyError, "manifest limit of 3 entries"):
                LocalReadOnlyExecutor.bind(self.fixture.target, self.fixture.scratch)

    def test_bind_refuses_target_over_byte_limit(self) -> None:
        with mock.patch.object(read_only_executor, "_MAX_MANIFEST_BYTES", 10):
            with self.assertRaisesRegex(ReadOnlyPolicyError, "manifest limit of 10 file bytes"):
                LocalReadOnlyExecutor.bind(self.fixture.target, self.fixture.scratch)

    def test_execute_fails_closed_when_target_grows_past_limit(self) -> None:
        executor = LocalReadOnlyExecutor.bind(self.fixture.target, self.fixture.scratch)
        with mock.patch.object(read_only_executor, "_MAX_MANIFEST_ENTRIES", 3):
            with self.assertRaisesRegex(ReadOnlyPolicyError, "manifest limit"):
                executor.execute(action("S1", "repo.read", path="README.md"))


if __name__ == "__main__":
    unittest.main()
