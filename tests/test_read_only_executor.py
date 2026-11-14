from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from nexus_workbench import ActionCell, ActionMode, ObservationOutcome
from nexus_workbench import read_only_executor
from nexus_workbench.read_only_executor import LocalReadOnlyExecutor, ReadOnlyPolicyError

TRUSTED_GIT = next(
    (c for c in ("/usr/bin/git", "/opt/homebrew/bin/git", "/usr/local/bin/git") if os.path.exists(c)),
    "/usr/bin/git",
)


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
        self.git_config_env = mock.patch.dict("os.environ", {"GIT_CONFIG_GLOBAL": os.devnull})
        self.git_config_env.start()
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
        self.git_config_env.stop()


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
        self.git_env = mock.patch.dict("os.environ", {
            "NEXUS_WORKBENCH_GIT_EXECUTABLE": TRUSTED_GIT,
            "GIT_NO_REPLACE_OBJECTS": "",
        })
        self.git_env.start()
        self.addCleanup(self.git_env.stop)
        self.config_path = mock.patch.object(read_only_executor, "_GIT_CONFIG_PATH", Path("/missing-workbench-git.conf"), create=True)
        self.config_path.start()
        self.addCleanup(self.config_path.stop)
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


    def test_git_default_discovery_works_without_an_override(self) -> None:
        with mock.patch.dict("os.environ"):
            os.environ.pop("NEXUS_WORKBENCH_GIT_EXECUTABLE", None)
            executor = LocalReadOnlyExecutor.bind(self.fixture.target, self.fixture.scratch)
        self.assertEqual(executor.git_source, "default")
        self.assertTrue(Path(executor.git_executable).is_absolute())

    def test_git_default_discovery_ignores_path_shim(self) -> None:
        with tempfile.TemporaryDirectory() as fake_bin:
            fake = Path(fake_bin) / "git"
            marker = Path(fake_bin) / "was-run"
            fake.write_text(f"#!/bin/sh\nprintf called > '{marker}'\nexit 0\n", encoding="utf-8")
            fake.chmod(0o755)
            with mock.patch.dict("os.environ", {"PATH": fake_bin}):
                os.environ.pop("NEXUS_WORKBENCH_GIT_EXECUTABLE", None)
                executor = LocalReadOnlyExecutor.bind(self.fixture.target, self.fixture.scratch)
            self.assertTrue(Path(executor.git_executable).is_absolute())
            self.assertFalse(marker.exists())

    def test_admin_config_precedes_defaults_when_environment_is_unset(self) -> None:
        config = Path("/etc/hosts")
        original_read_text = Path.read_text

        def read_config(path, *args, **kwargs):
            if path.name == "hosts":
                return TRUSTED_GIT
            return original_read_text(path, *args, **kwargs)

        with mock.patch.object(read_only_executor, "_GIT_CONFIG_PATH", config), \
             mock.patch.object(Path, "read_text", read_config), \
             mock.patch.dict("os.environ"):
            os.environ.pop("NEXUS_WORKBENCH_GIT_EXECUTABLE", None)
            executor = LocalReadOnlyExecutor.bind(self.fixture.target, self.fixture.scratch)
        self.assertEqual(executor.git_source, "config")
        self.assertEqual(executor.git_executable, str(Path(TRUSTED_GIT).resolve()))

    def test_caller_writable_override_is_rejected_before_version_launch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wrapper = Path(tmp) / "git"
            marker = Path(tmp) / "was-run"
            wrapper.write_text(
                f"#!/bin/sh\nif [ \"$1\" = --version ]; then echo 'git version fake'; exit 0; fi\n"
                f"printf called > '{marker}'\nexec {TRUSTED_GIT} \"$@\"\n",
                encoding="utf-8",
            )
            wrapper.chmod(0o755)
            with mock.patch.dict("os.environ", {"NEXUS_WORKBENCH_GIT_EXECUTABLE": str(wrapper)}):
                with self.assertRaisesRegex(ReadOnlyPolicyError, "NEXUS_WORKBENCH_GIT_EXECUTABLE"):
                    LocalReadOnlyExecutor.bind(self.fixture.target, self.fixture.scratch)
            self.assertFalse(marker.exists())

    def test_git_evidence_matches_the_executable_passed_to_launcher(self) -> None:
        executor = self.executor
        launched: list[list[str]] = []
        replace_guard_values: list[str | None] = []
        real_run = subprocess.run

        def record_run(args, *pargs, **kwargs):
            if isinstance(args, (list, tuple)) and executor.git_executable in args:
                launched.append(list(args))
                replace_guard_values.append(kwargs.get("env", {}).get("GIT_NO_REPLACE_OBJECTS"))
            return real_run(args, *pargs, **kwargs)

        with mock.patch.object(subprocess, "run", side_effect=record_run):
            executor._git("rev-parse", "HEAD")
        evidence = executor.snapshot().summary()
        self.assertTrue(launched)
        self.assertTrue(replace_guard_values)
        self.assertTrue(all(value == "1" for value in replace_guard_values))
        self.assertIn(executor.git_executable, launched[-1])
        self.assertEqual(evidence["git_sha256"], read_only_executor._sha256_bytes(Path(executor.git_executable).read_bytes()))
        self.assertEqual(evidence["git_source"], "environment")
        self.assertTrue(evidence["git_owner"])
        self.assertTrue(evidence["git_mode"])
        self.assertTrue(evidence["git_version"])
        self.assertTrue(evidence["git_replace_ref_check_passed"])

class ManifestLimitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.git_env = mock.patch.dict("os.environ", {"GIT_NO_REPLACE_OBJECTS": ""})
        self.git_env.start()
        self.addCleanup(self.git_env.stop)
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

    def test_bind_rejects_replace_refs(self) -> None:
        git(self.fixture.target, "replace", "HEAD", "HEAD")
        with self.assertRaisesRegex(ReadOnlyPolicyError, "replace refs"):
            LocalReadOnlyExecutor.bind(self.fixture.target, self.fixture.scratch)

    def test_git_subprocess_env_is_allowlisted(self) -> None:
        recorded: list[dict[str, str]] = []
        real_run = subprocess.run

        def spy(*args, **kwargs):
            recorded.append(dict(kwargs.get("env") or {}))
            return real_run(*args, **kwargs)

        poisoned = {
            "LD_PRELOAD": "/nonexistent.so",
            "DYLD_INSERT_LIBRARIES": "/nonexistent.dylib",
            "SECRET_TOKEN": "x",
            "GIT_CONFIG_COUNT": "1",
        }
        with mock.patch.dict("os.environ", poisoned):
            executor = LocalReadOnlyExecutor.bind(self.fixture.target, self.fixture.scratch)
            with mock.patch.object(read_only_executor.subprocess, "run", side_effect=spy):
                executor._git("rev-parse", "--show-toplevel")
        self.assertTrue(recorded)
        for env in recorded:
            for key in poisoned.keys() - {"GIT_CONFIG_COUNT"}:
                self.assertNotIn(key, env)
        self.assertNotEqual(recorded[-1].get("GIT_CONFIG_COUNT"), "1")

    def test_bind_rejects_grafts(self) -> None:
        info = self.fixture.target / ".git" / "info"
        info.mkdir(exist_ok=True)
        head = git(self.fixture.target, "rev-parse", "HEAD").strip()
        (info / "grafts").write_text(f"{head}\n", encoding="utf-8")
        with self.assertRaisesRegex(ReadOnlyPolicyError, "graft"):
            LocalReadOnlyExecutor.bind(self.fixture.target, self.fixture.scratch)

    def test_bind_rejects_alternates(self) -> None:
        info = self.fixture.target / ".git" / "objects" / "info"
        info.mkdir(parents=True, exist_ok=True)
        (info / "alternates").write_text(str(self.fixture.outside.parent) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ReadOnlyPolicyError, "alternate"):
            LocalReadOnlyExecutor.bind(self.fixture.target, self.fixture.scratch)

    def test_bind_rejects_symlinked_git_metadata_files(self) -> None:
        head = self.fixture.target / ".git" / "HEAD"
        external = self.fixture.root / "external-head"
        head.rename(external)
        head.symlink_to(external)
        with self.assertRaisesRegex(ReadOnlyPolicyError, "symlink"):
            LocalReadOnlyExecutor.bind(self.fixture.target, self.fixture.scratch)

    def test_bind_rejects_hardlinked_git_config(self) -> None:
        config = self.fixture.target / ".git" / "config"
        external = self.fixture.root / "external-config"
        os.link(config, external)
        with self.assertRaisesRegex(ReadOnlyPolicyError, "hard.link|hardlink"):
            LocalReadOnlyExecutor.bind(self.fixture.target, self.fixture.scratch)

    def test_bind_rejects_execution_capable_config_at_any_scope(self) -> None:
        git(self.fixture.target, "config", "diff.hostile.textconv", "touch /tmp/workbench-pwned")
        with self.assertRaisesRegex(ReadOnlyPolicyError, "untrusted Git config"):
            LocalReadOnlyExecutor.bind(self.fixture.target, self.fixture.scratch)

    def test_bind_ignores_global_filter_config_but_rejects_local_filter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            global_config = Path(tmp) / "gitconfig"
            subprocess.run(["git", "config", "--file", str(global_config), "filter.lfs.clean", "git-lfs clean %f"],
                           check=True, capture_output=True)
            with mock.patch.dict("os.environ", {"GIT_CONFIG_GLOBAL": str(global_config)}):
                LocalReadOnlyExecutor.bind(self.fixture.target, self.fixture.scratch)

        git(self.fixture.target, "config", "filter.local.clean", "hostile")
        with self.assertRaisesRegex(ReadOnlyPolicyError, "untrusted Git config"):
            LocalReadOnlyExecutor.bind(self.fixture.target, self.fixture.scratch)

    def test_bind_ignores_execution_capable_system_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            system_config = Path(tmp) / "gitconfig"
            subprocess.run(["git", "config", "--file", str(system_config), "core.fsmonitor", "hostile"],
                           check=True, capture_output=True)
            with mock.patch.dict("os.environ", {"GIT_CONFIG_SYSTEM": str(system_config)}):
                LocalReadOnlyExecutor.bind(self.fixture.target, self.fixture.scratch)

    def test_bind_rejects_each_execution_capable_config_family(self) -> None:
        keys = (
            "filter.hostile.process", "filter.hostile.clean", "filter.hostile.smudge",
            "diff.hostile.command", "core.fsmonitor", "core.hooksPath", "include.path",
            "includeIf.gitdir:/tmp/.path", "extensions.worktreeConfig",
        )
        for index, key in enumerate(keys):
            with self.subTest(key=key):
                fixture = RepoFixture()
                try:
                    value = "true" if key == "extensions.worktreeConfig" else "/tmp/hostile"
                    git(fixture.target, "config", key, value)
                    with self.assertRaisesRegex(ReadOnlyPolicyError, "untrusted Git config"):
                        LocalReadOnlyExecutor.bind(fixture.target, fixture.scratch)
                finally:
                    fixture.close()

    def test_git_invocation_overrides_execution_capable_config(self) -> None:
        executor = LocalReadOnlyExecutor.bind(self.fixture.target, self.fixture.scratch)
        recorded: list[dict[str, str]] = []
        real_run = subprocess.run

        def spy(*args, **kwargs):
            if args and isinstance(args[0], list) and args[0] and args[0][0] == sys.executable:
                recorded.append(dict(kwargs.get("env") or {}))
            return real_run(*args, **kwargs)

        with mock.patch.object(read_only_executor.subprocess, "run", side_effect=spy):
            executor._git("rev-parse", "HEAD")
        self.assertTrue(recorded)
        env = recorded[-1]
        self.assertEqual(env.get("GIT_CONFIG_NOSYSTEM"), "1")
        self.assertEqual(env.get("GIT_CONFIG_GLOBAL"), os.devnull)
        self.assertGreaterEqual(int(env.get("GIT_CONFIG_COUNT", "0")), 10)
        values = [env.get(f"GIT_CONFIG_KEY_{i}", "") + "=" + env.get(f"GIT_CONFIG_VALUE_{i}", "")
                  for i in range(int(env.get("GIT_CONFIG_COUNT", "0")))]
        self.assertIn("core.sshCommand=", values)
        self.assertIn("credential.helper=", values)
        self.assertIn("protocol.ext.allow=never", values)
        for setting in (
            "core.fsmonitor=false", "core.hooksPath=/dev/null", "core.pager=cat",
            "core.editor=", "core.askPass=", "diff.external=", "include.path=/dev/null",
            "includeIf.gitdir:/__nexus_workbench_never__/.path=/dev/null",
        ):
            self.assertIn(setting, values)

    def test_git_fails_closed_when_python_executable_is_not_absolute(self) -> None:
        executor = LocalReadOnlyExecutor.bind(self.fixture.target, self.fixture.scratch)
        with mock.patch.object(read_only_executor.sys, "executable", "python3"):
            with self.assertRaisesRegex(ReadOnlyPolicyError, "absolute Python"):
                executor._git("rev-parse", "HEAD")

    def test_git_fails_closed_when_python_executable_is_missing(self) -> None:
        executor = LocalReadOnlyExecutor.bind(self.fixture.target, self.fixture.scratch)
        with mock.patch.object(read_only_executor.sys, "executable", "/missing/python"):
            with self.assertRaisesRegex(ReadOnlyPolicyError, "absolute Python"):
                executor._git("rev-parse", "HEAD")

    def test_diff_disables_external_diff_and_textconv(self) -> None:
        executor = LocalReadOnlyExecutor.bind(self.fixture.target, self.fixture.scratch)
        real_run = subprocess.run
        launched: list[list[str]] = []

        def record(args, *pargs, **kwargs):
            if isinstance(args, (list, tuple)) and executor.git_executable in args:
                launched.append(list(args))
            return real_run(args, *pargs, **kwargs)

        with mock.patch.object(subprocess, "run", side_effect=record):
            executor._git("diff")
        self.assertTrue(launched)
        self.assertIn("--no-ext-diff", launched[-1])
        self.assertIn("--no-textconv", launched[-1])

    def test_git_rechecks_metadata_inode_after_run(self) -> None:
        executor = LocalReadOnlyExecutor.bind(self.fixture.target, self.fixture.scratch)
        config = self.fixture.target / ".git" / "config"
        real_run = subprocess.run
        swapped = False

        def swap_after_git(*args, **kwargs):
            nonlocal swapped
            result = real_run(*args, **kwargs)
            if not swapped and args and isinstance(args[0], list) and args[0][0] == sys.executable:
                replacement = config.with_suffix(".new")
                replacement.write_bytes(config.read_bytes())
                replacement.replace(config)
                swapped = True
            return result

        with mock.patch.object(read_only_executor.subprocess, "run", side_effect=swap_after_git):
            with self.assertRaisesRegex(ReadOnlyPolicyError, "changed during|Git metadata"):
                executor._git("rev-parse", "HEAD")
        self.assertTrue(swapped)

    def test_bind_rejects_objects_symlink_to_external_database(self) -> None:
        objects = self.fixture.target / ".git" / "objects"
        external = self.fixture.root / "external-objects"
        objects.rename(external)
        objects.symlink_to(external, target_is_directory=True)
        with self.assertRaisesRegex(ReadOnlyPolicyError, "symlink|outside"):
            LocalReadOnlyExecutor.bind(self.fixture.target, self.fixture.scratch)

    def test_bind_rejects_refs_symlink_to_external_directory(self) -> None:
        refs = self.fixture.target / ".git" / "refs"
        external = self.fixture.root / "external-refs"
        refs.rename(external)
        refs.symlink_to(external, target_is_directory=True)
        with self.assertRaisesRegex(ReadOnlyPolicyError, "symlink|outside"):
            LocalReadOnlyExecutor.bind(self.fixture.target, self.fixture.scratch)

    def test_bind_rejects_git_dir_outside_target(self) -> None:
        external = self.fixture.root / "external-gitdir"
        shutil.move(str(self.fixture.target / ".git"), str(external))
        (self.fixture.target / ".git").write_text(f"gitdir: {external}\n", encoding="utf-8")
        with self.assertRaisesRegex(ReadOnlyPolicyError, "outside"):
            LocalReadOnlyExecutor.bind(self.fixture.target, self.fixture.scratch)

    def test_git_executable_ignores_path_wrapper_for_trusted_absolute_path(self) -> None:
        real_git = TRUSTED_GIT
        with tempfile.TemporaryDirectory() as fake_bin:
            fake = Path(fake_bin) / "git"
            marker = Path(fake_bin) / "was-run"
            fake.write_text(
                f"#!/bin/sh\nprintf called > '{marker}'\nexec {real_git} \"$@\"\n",
                encoding="utf-8",
            )
            fake.chmod(0o755)
            with mock.patch.dict("os.environ", {
                "PATH": fake_bin,
                "NEXUS_WORKBENCH_GIT_EXECUTABLE": real_git,
            }):
                executor = LocalReadOnlyExecutor.bind(self.fixture.target, self.fixture.scratch)
                top = executor._git("rev-parse", "--show-toplevel").strip()
            self.assertEqual(Path(top), self.fixture.target.resolve())
            self.assertFalse(marker.exists(), "Git was substituted by the caller's PATH wrapper")
            self.assertEqual(Path(executor.git_executable), Path(real_git).resolve())
            self.assertTrue(executor.git_version)

    def test_git_executable_fails_closed_without_trusted_absolute_path(self) -> None:
        with mock.patch.dict("os.environ", {"NEXUS_WORKBENCH_GIT_EXECUTABLE": ""}):
            with self.assertRaisesRegex(ReadOnlyPolicyError, "NEXUS_WORKBENCH_GIT_EXECUTABLE"):
                LocalReadOnlyExecutor.bind(self.fixture.target, self.fixture.scratch)

    def test_git_environment_overrides_cannot_redirect_snapshot(self) -> None:
        executor = LocalReadOnlyExecutor.bind(self.fixture.target, self.fixture.scratch)
        foreign = self.fixture.root / "foreign.git"
        subprocess.run(["git", "init", "--bare", str(foreign)], check=True, capture_output=True)
        subprocess.run(["git", "--git-dir", str(foreign), "--work-tree", str(self.fixture.target), "add", "-A"], check=True, capture_output=True)
        with mock.patch.dict("os.environ", {
            "GIT_DIR": str(foreign),
            "GIT_WORK_TREE": str(self.fixture.root),
            "GIT_INDEX_FILE": str(foreign / "index"),
            "GIT_NAMESPACE": "forged",
        }):
            self.assertEqual(Path(executor._git("rev-parse", "--show-toplevel").strip()), self.fixture.target.resolve())
            self.assertEqual(executor._git("rev-parse", "HEAD").strip(), executor.baseline.head)


class DescriptorRaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = RepoFixture()
        self.executor = LocalReadOnlyExecutor.bind(self.fixture.target, self.fixture.scratch)

    def tearDown(self) -> None:
        self.fixture.close()

    def test_repo_read_does_not_follow_replacement_symlink(self) -> None:
        path = self.fixture.target / "README.md"
        original = self.executor._target_path
        swapped = False

        def swap_after_check(value):
            nonlocal swapped
            resolved = original(value)
            if value == "README.md" and not swapped:
                path.unlink()
                path.symlink_to(self.fixture.outside)
                swapped = True
            return resolved

        with mock.patch.object(self.executor, "_target_path", swap_after_check):
            try:
                observed = self.executor._repo_read({"op": "repo.read", "path": "README.md"})
            except (OSError, ReadOnlyPolicyError):
                observed = {"text": ""}
        self.assertTrue(swapped)
        self.assertNotEqual(observed["text"], "outside secret\n")

    def test_snapshot_git_uses_pinned_target_after_path_replacement(self) -> None:
        pinned_head = self.executor.baseline.head
        target = self.fixture.target
        displaced = self.fixture.root / "displaced-target"
        replacement = self.fixture.root / "replacement"
        replacement.mkdir()
        git(replacement, "init")
        git(replacement, "config", "user.email", "replacement@example.invalid")
        git(replacement, "config", "user.name", "Replacement")
        (replacement / "README.md").write_text("replacement tree\n", encoding="utf-8")
        git(replacement, "add", ".")
        git(replacement, "commit", "-m", "replacement")
        swapped = False
        original_git = self.executor._git

        def swap_before_git(*args):
            nonlocal swapped
            if not swapped:
                target.rename(displaced)
                replacement.rename(target)
                swapped = True
            return original_git(*args)

        with mock.patch.object(self.executor, "_git", side_effect=swap_before_git):
            snapshot = self.executor.snapshot()
        self.assertTrue(swapped, "the target path replacement must occur during the snapshot")
        self.assertEqual(snapshot.head, pinned_head)

    def test_repo_search_does_not_follow_replacement_symlink(self) -> None:
        path = self.fixture.target / "README.md"
        original_is_file = Path.is_file
        original_open_path = getattr(read_only_executor, "open_path", None)
        swapped = False

        def replace_candidate() -> None:
            nonlocal swapped
            if not swapped:
                path.unlink()
                path.symlink_to(self.fixture.outside)
                swapped = True

        def is_file_then_swap(candidate, *args, **kwargs):
            result = original_is_file(candidate, *args, **kwargs)
            if candidate.name == path.name:
                replace_candidate()
            return result

        def open_then_swap(*args, **kwargs):
            if args[1][-1] == path.name:
                replace_candidate()
            return original_open_path(*args, **kwargs)

        with mock.patch.object(Path, "is_file", is_file_then_swap), mock.patch.object(
            read_only_executor, "open_path", open_then_swap, create=True
        ):
            try:
                observed = self.executor._repo_search({"op": "repo.search", "path": ".", "query": "outside secret", "max_results": 10})
            except (OSError, ReadOnlyPolicyError, ValueError):
                observed = {"matches": []}
        self.assertTrue(swapped)
        self.assertEqual(observed["matches"], [])

    def test_test_discover_does_not_follow_replacement_symlink(self) -> None:
        path = self.fixture.target / "tests" / "test_safe.py"
        original_resolve = Path.resolve
        original_open_path = getattr(read_only_executor, "open_path", None)
        swapped = False

        def replace_candidate() -> None:
            nonlocal swapped
            if not swapped:
                self.fixture.outside.write_text("def test_outside_secret_marker():\n    pass\n", encoding="utf-8")
                path.unlink()
                path.symlink_to(self.fixture.outside)
                swapped = True

        def resolve_then_replace(candidate, *args, **kwargs):
            result = original_resolve(candidate, *args, **kwargs)
            if candidate.name == path.name:
                replace_candidate()
            return result

        def open_then_replace(*args, **kwargs):
            if args[1][-1] == path.name:
                replace_candidate()
            return original_open_path(*args, **kwargs)

        with mock.patch.object(Path, "resolve", resolve_then_replace), mock.patch.object(
            read_only_executor, "open_path", open_then_replace, create=True
        ):
            try:
                observed = self.executor._test_discover({"op": "test.discover", "path": "tests"})
            except (OSError, ReadOnlyPolicyError):
                observed = {"tests": [], "files": []}
        self.assertTrue(swapped)
        self.assertNotIn("test_outside_secret_marker", str(observed))

    def test_manifest_fingerprints_opened_regular_file(self) -> None:
        path = self.fixture.target / "README.md"
        original_open = os.open
        original_path_open = Path.open
        swapped = False

        def replace_candidate() -> None:
            nonlocal swapped
            if not swapped:
                path.unlink()
                path.symlink_to(self.fixture.outside)
                swapped = True

        def swap_then_open(candidate, flags, *args, **kwargs):
            if candidate == path.name and kwargs.get("dir_fd") is not None:
                replace_candidate()
            return original_open(candidate, flags, *args, **kwargs)

        def swap_then_path_open(candidate, *args, **kwargs):
            if candidate.name == path.name:
                replace_candidate()
            return original_path_open(candidate, *args, **kwargs)

        with mock.patch.object(os, "open", swap_then_open), mock.patch.object(Path, "open", swap_then_path_open):
            try:
                entries = read_only_executor._manifest(self.fixture.target)
            except OSError:
                entries = {}
        self.assertTrue(swapped)
        outside_hash = read_only_executor._sha256_bytes(self.fixture.outside.read_bytes())
        self.assertNotIn(outside_hash, str(entries.get("README.md", "")))

    def test_artifact_parent_swap_cannot_redirect_write(self) -> None:
        outside = self.fixture.root / "escaped"
        original = self.executor._scratch_path
        swapped = False

        def swap_after_validation(value):
            nonlocal swapped
            result = original(value)
            parent = self.fixture.scratch / "notes"
            if not swapped:
                parent.mkdir(exist_ok=True)
                parent.rmdir()
                parent.symlink_to(self.fixture.root, target_is_directory=True)
                swapped = True
            return result

        with mock.patch.object(self.executor, "_scratch_path", swap_after_validation):
            try:
                self.executor._artifact_write({"op": "artifact.write", "path": "notes/escaped", "content": "secret"})
            except (OSError, ReadOnlyPolicyError):
                pass
        self.assertTrue(swapped)
        self.assertFalse(outside.exists())


if __name__ == "__main__":
    unittest.main()
