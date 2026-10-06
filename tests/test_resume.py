from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

from nexus_workbench import (
    G4_CLAIM_CEILING,
    ActionCell,
    ActionMode,
    ArtifactRecord,
    JsonWorkbenchStore,
    ObservationBundle,
    ObservationOutcome,
    ResumeBlocked,
    ResumeCheckpoint,
    ResumeCoordinator,
    ResumeDisposition,
    ResumePhase,
    ResumeRequest,
    WorkbenchNotebook,
)

SESSION = "wb-g4"
TASK = "task-g4"
OPERATION = "op-g4"
ATTEMPT = "attempt-g4"
TARGET = "git-tree:fixture-g4"


def action(step_id: str, revision: int = 1, intent: str | None = None) -> ActionCell:
    return ActionCell(
        workbench_session_id=SESSION,
        step_id=step_id,
        notebook_revision=revision,
        intent=intent or f"perform {step_id}",
        mode=ActionMode.READ_ONLY_PROBE,
        requested_capabilities=("repo.read",),
        code_or_action=json.dumps({"op": "repo.read", "path": "README.md"}),
    )


def observation(
    cell: ActionCell,
    outcome: ObservationOutcome = ObservationOutcome.SUCCEEDED,
) -> ObservationBundle:
    return ObservationBundle(
        workbench_session_id=SESSION,
        step_id=cell.step_id,
        action_hash=cell.content_hash,
        executor_identity="fixture-executor",
        started_at="2026-10-06T12:00:00Z",
        finished_at="2026-10-06T12:00:01Z",
        outcome=outcome,
        result_summary="durable observation",
        physical_readback={"target_identity": TARGET},
    )


class DurableFixture:
    def __init__(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = JsonWorkbenchStore(self.root)
        self.coordinator = ResumeCoordinator(self.store)

        self.s1 = action("S1")
        self.o1 = observation(self.s1)
        self.a1 = ArtifactRecord(
            workbench_session_id=SESSION,
            artifact_id="A1",
            media_type="application/json",
            payload={"summary": "durable artifact", "source_step": "S1"},
        )
        self.notebook = WorkbenchNotebook(
            workbench_session_id=SESSION,
            task_id=TASK,
            operation_id=OPERATION,
            attempt_id=ATTEMPT,
            target_identity=TARGET,
            goal="resume after worker replacement",
            current_objective="continue from durable evidence",
            next_probe="execute S2",
            observation_refs=("S1",),
            artifact_refs=("A1",),
            revision=1,
        )
        self.s2 = action("S2")
        self.cp1 = ResumeCheckpoint(
            checkpoint_id="cp-1",
            workbench_session_id=SESSION,
            task_id=TASK,
            operation_id=OPERATION,
            attempt_id=ATTEMPT,
            target_identity=TARGET,
            sequence=1,
            notebook_revision=1,
            notebook_hash=self.notebook.content_hash,
            phase=ResumePhase.READY_FOR_ACTION,
            completed_step_ids=("S1",),
            observation_refs=("S1",),
            artifact_refs=("A1",),
            next_action=self.s2,
        )

        self.store.save_action(self.s1)
        self.store.save_observation(self.o1)
        self.store.save_artifact(self.a1)
        self.store.save_notebook(self.notebook)
        self.coordinator.publish(self.cp1)

    def request(
        self,
        *,
        task_id: str = TASK,
        operation_id: str = OPERATION,
        attempt_id: str = ATTEMPT,
        target_identity: str = TARGET,
        resumer_identity: str = "worker-B",
        resumer_model_identity: str = "model-B",
    ) -> ResumeRequest:
        return ResumeRequest(
            workbench_session_id=SESSION,
            task_id=task_id,
            operation_id=operation_id,
            attempt_id=attempt_id,
            target_identity=target_identity,
            resumer_identity=resumer_identity,
            resumer_model_identity=resumer_model_identity,
        )

    def close(self) -> None:
        self.temp.cleanup()


class ResumeContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = DurableFixture()

    def tearDown(self) -> None:
        self.fx.close()

    def test_resume_reconstructs_without_chat_provider_or_original_model(self) -> None:
        receipt = self.fx.coordinator.resume(self.fx.request())
        self.assertEqual(receipt.disposition, ResumeDisposition.READY_FOR_ACTION)
        self.assertEqual(receipt.next_action, self.fx.s2)
        self.assertEqual(receipt.resumer_identity, "worker-B")
        self.assertEqual(receipt.resumer_model_identity, "model-B")
        self.assertEqual(receipt.claim_ceiling, G4_CLAIM_CEILING)
        raw = json.dumps(receipt.to_dict(), sort_keys=True).lower()
        self.assertNotIn("transcript", raw)
        self.assertNotIn("provider_session", raw)
        self.assertNotIn("model-a", raw)

    def test_identity_substitution_fails_closed(self) -> None:
        cases = (
            self.fx.request(task_id="other-task"),
            self.fx.request(operation_id="other-operation"),
            self.fx.request(attempt_id="other-attempt"),
            self.fx.request(target_identity="other-target"),
        )
        for request in cases:
            with self.subTest(request=request):
                with self.assertRaisesRegex(ResumeBlocked, "IDENTITY_MISMATCH"):
                    self.fx.coordinator.resume(request)

    def test_stale_notebook_and_rollback_writer_fail_closed(self) -> None:
        revision2 = WorkbenchNotebook(
            workbench_session_id=SESSION,
            task_id=TASK,
            operation_id=OPERATION,
            attempt_id=ATTEMPT,
            target_identity=TARGET,
            goal=self.fx.notebook.goal,
            current_objective="new durable objective",
            next_probe="new durable probe",
            observation_refs=("S1",),
            artifact_refs=("A1",),
            revision=2,
        )
        self.fx.store.save_notebook(revision2)
        with self.assertRaisesRegex(ResumeBlocked, "STALE_NOTEBOOK_REVISION"):
            self.fx.coordinator.resume(self.fx.request())
        with self.assertRaisesRegex(ValueError, "revision must advance"):
            self.fx.store.save_notebook(self.fx.notebook)

    def test_missing_completed_observation_fails_closed(self) -> None:
        path = self.fx.root / "observations" / SESSION / "S1.json"
        path.unlink()
        with self.assertRaisesRegex(ResumeBlocked, "DURABLE_REFERENCE_MISSING"):
            self.fx.coordinator.resume(self.fx.request())

    def test_tampered_artifact_fails_closed(self) -> None:
        path = self.fx.root / "artifacts" / SESSION / "A1.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["payload"]["summary"] = "tampered"
        path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "artifact hash mismatch"):
            self.fx.coordinator.resume(self.fx.request())

    def test_tampered_and_duplicate_key_checkpoint_fail_closed(self) -> None:
        path = self.fx.root / "resume-checkpoints" / SESSION / "cp-1.json"
        original = path.read_text(encoding="utf-8")
        payload = json.loads(original)
        payload["target_identity"] = "changed"
        path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "checkpoint hash mismatch"):
            self.fx.coordinator.resume(self.fx.request())

        path.write_text(
            original.replace(
                '"sequence": 1,',
                '"sequence": 2,\n  "sequence": 1,',
                1,
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "duplicate JSON key: sequence"):
            self.fx.coordinator.resume(self.fx.request())

    def test_materialized_next_step_blocks_blind_replay(self) -> None:
        self.fx.store.save_action(self.fx.s2)
        with self.assertRaisesRegex(ResumeBlocked, "NEXT_STEP_ALREADY_MATERIALIZED"):
            self.fx.coordinator.resume(self.fx.request())

    def test_inflight_and_outcome_unknown_resume_only_to_reconciliation(self) -> None:
        self.fx.store.save_action(self.fx.s2)
        cp2 = ResumeCheckpoint(
            checkpoint_id="cp-2",
            workbench_session_id=SESSION,
            task_id=TASK,
            operation_id=OPERATION,
            attempt_id=ATTEMPT,
            target_identity=TARGET,
            sequence=2,
            previous_checkpoint_id=self.fx.cp1.checkpoint_id,
            previous_checkpoint_hash=self.fx.cp1.checkpoint_hash,
            notebook_revision=1,
            notebook_hash=self.fx.notebook.content_hash,
            phase=ResumePhase.IN_FLIGHT,
            completed_step_ids=("S1",),
            observation_refs=("S1",),
            artifact_refs=("A1",),
            active_action=self.fx.s2,
        )
        self.fx.coordinator.publish(cp2)

        receipt = self.fx.coordinator.resume(self.fx.request())
        self.assertEqual(receipt.disposition, ResumeDisposition.RECONCILE_REQUIRED)
        self.assertEqual(receipt.reconcile_step_id, "S2")
        self.assertIsNone(receipt.next_action)

        self.fx.store.save_observation(
            observation(self.fx.s2, ObservationOutcome.OUTCOME_UNKNOWN)
        )
        receipt = self.fx.coordinator.resume(self.fx.request())
        self.assertEqual(receipt.disposition, ResumeDisposition.RECONCILE_REQUIRED)
        self.assertIsNone(receipt.next_action)

    def test_lineage_fork_and_stale_head_are_rejected(self) -> None:
        self.fx.store.save_action(self.fx.s2)
        cp2 = ResumeCheckpoint(
            checkpoint_id="cp-2",
            workbench_session_id=SESSION,
            task_id=TASK,
            operation_id=OPERATION,
            attempt_id=ATTEMPT,
            target_identity=TARGET,
            sequence=2,
            previous_checkpoint_id="cp-1",
            previous_checkpoint_hash=self.fx.cp1.checkpoint_hash,
            notebook_revision=1,
            notebook_hash=self.fx.notebook.content_hash,
            phase=ResumePhase.IN_FLIGHT,
            completed_step_ids=("S1",),
            observation_refs=("S1",),
            artifact_refs=("A1",),
            active_action=self.fx.s2,
        )
        self.fx.coordinator.publish(cp2)

        fork = ResumeCheckpoint(
            checkpoint_id="cp-2-fork",
            workbench_session_id=SESSION,
            task_id=TASK,
            operation_id=OPERATION,
            attempt_id=ATTEMPT,
            target_identity=TARGET,
            sequence=2,
            previous_checkpoint_id="cp-1",
            previous_checkpoint_hash=self.fx.cp1.checkpoint_hash,
            notebook_revision=1,
            notebook_hash=self.fx.notebook.content_hash,
            phase=ResumePhase.IN_FLIGHT,
            completed_step_ids=("S1",),
            observation_refs=("S1",),
            artifact_refs=("A1",),
            active_action=self.fx.s2,
        )
        with self.assertRaisesRegex(ValueError, "sequence does not follow durable head"):
            self.fx.coordinator.publish(fork)

    def test_completed_step_cannot_be_selected_again(self) -> None:
        with self.assertRaisesRegex(ValueError, "already completed"):
            ResumeCheckpoint(
                checkpoint_id="bad",
                workbench_session_id=SESSION,
                task_id=TASK,
                operation_id=OPERATION,
                attempt_id=ATTEMPT,
                target_identity=TARGET,
                sequence=1,
                notebook_revision=1,
                notebook_hash=self.fx.notebook.content_hash,
                phase=ResumePhase.READY_FOR_ACTION,
                completed_step_ids=("S1",),
                observation_refs=("S1",),
                artifact_refs=("A1",),
                next_action=self.fx.s1,
            )

    def test_action_observation_and_artifact_records_are_immutable(self) -> None:
        changed_action = action("S1", intent="different meaning")
        with self.assertRaisesRegex(ValueError, "ActionCell is immutable"):
            self.fx.store.save_action(changed_action)

        changed_observation = observation(
            self.fx.s1, ObservationOutcome.FAILED
        )
        with self.assertRaisesRegex(ValueError, "ObservationBundle is immutable"):
            self.fx.store.save_observation(changed_observation)

        changed_artifact = ArtifactRecord(
            workbench_session_id=SESSION,
            artifact_id="A1",
            media_type="application/json",
            payload={"summary": "different"},
        )
        with self.assertRaisesRegex(ValueError, "ArtifactRecord is immutable"):
            self.fx.store.save_artifact(changed_artifact)


class ReviewerRegressionTests(unittest.TestCase):
    def test_durable_paths_do_not_alias_session_and_step_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonWorkbenchStore(Path(tmp))
            left = ActionCell(
                "a--b",
                "S1",
                1,
                "left",
                ActionMode.READ_ONLY_PROBE,
                ("repo.read",),
            )
            right = ActionCell(
                "a",
                "b--S1",
                1,
                "right",
                ActionMode.READ_ONLY_PROBE,
                ("repo.read",),
            )
            store.save_action(left)
            store.save_action(right)
            self.assertEqual(store.load_action("a--b", "S1"), left)
            self.assertEqual(store.load_action("a", "b--S1"), right)
            self.assertNotEqual(
                store._path("actions", "a--b", "S1"),
                store._path("actions", "a", "b--S1"),
            )

    def test_reconciliation_cannot_substitute_inflight_action(self) -> None:
        fx = DurableFixture()
        try:
            fx.store.save_action(fx.s2)
            cp2 = ResumeCheckpoint(
                checkpoint_id="cp-2",
                workbench_session_id=SESSION,
                task_id=TASK,
                operation_id=OPERATION,
                attempt_id=ATTEMPT,
                target_identity=TARGET,
                sequence=2,
                previous_checkpoint_id=fx.cp1.checkpoint_id,
                previous_checkpoint_hash=fx.cp1.checkpoint_hash,
                notebook_revision=1,
                notebook_hash=fx.notebook.content_hash,
                phase=ResumePhase.IN_FLIGHT,
                completed_step_ids=("S1",),
                observation_refs=("S1",),
                artifact_refs=("A1",),
                active_action=fx.s2,
            )
            fx.coordinator.publish(cp2)
            substituted = action("S3")
            cp3 = ResumeCheckpoint(
                checkpoint_id="cp-3",
                workbench_session_id=SESSION,
                task_id=TASK,
                operation_id=OPERATION,
                attempt_id=ATTEMPT,
                target_identity=TARGET,
                sequence=3,
                previous_checkpoint_id=cp2.checkpoint_id,
                previous_checkpoint_hash=cp2.checkpoint_hash,
                notebook_revision=1,
                notebook_hash=fx.notebook.content_hash,
                phase=ResumePhase.RECONCILE_REQUIRED,
                completed_step_ids=("S1",),
                observation_refs=("S1",),
                artifact_refs=("A1",),
                active_action=substituted,
            )
            with self.assertRaisesRegex(ResumeBlocked, "ACTION_SUBSTITUTION"):
                fx.coordinator.publish(cp3)
        finally:
            fx.close()

    def test_competing_processes_cannot_both_receive_ready_claim(self) -> None:
        fx = DurableFixture()
        try:
            barrier = fx.root / "start"
            consumer = textwrap.dedent(
                r"""
                import json, sys, time
                from pathlib import Path
                from nexus_workbench import JsonWorkbenchStore, ResumeCoordinator, ResumeRequest

                root = Path(sys.argv[1])
                barrier = Path(sys.argv[2])
                while not barrier.exists():
                    time.sleep(0.001)
                receipt = ResumeCoordinator(JsonWorkbenchStore(root)).resume(
                    ResumeRequest(
                        workbench_session_id="wb-g4",
                        task_id="task-g4",
                        operation_id="op-g4",
                        attempt_id="attempt-g4",
                        target_identity="git-tree:fixture-g4",
                        resumer_identity=sys.argv[3],
                        resumer_model_identity=sys.argv[4],
                    )
                )
                print(json.dumps(receipt.to_dict(), sort_keys=True))
                """
            )
            processes = [
                subprocess.Popen(
                    [
                        sys.executable,
                        "-c",
                        consumer,
                        str(fx.root),
                        str(barrier),
                        "worker-race-A",
                        "model-race-A",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=os.environ.copy(),
                ),
                subprocess.Popen(
                    [
                        sys.executable,
                        "-c",
                        consumer,
                        str(fx.root),
                        str(barrier),
                        "worker-race-B",
                        "model-race-B",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=os.environ.copy(),
                ),
            ]
            barrier.write_text("go\n", encoding="utf-8")
            results = []
            for process in processes:
                stdout, stderr = process.communicate(timeout=10)
                self.assertEqual(process.returncode, 0, stderr)
                results.append(json.loads(stdout))
            dispositions = sorted(item["disposition"] for item in results)
            self.assertEqual(
                dispositions,
                ["READY_FOR_ACTION", "RECONCILE_REQUIRED"],
            )
            ready = next(
                item for item in results if item["disposition"] == "READY_FOR_ACTION"
            )
            reconcile = next(
                item
                for item in results
                if item["disposition"] == "RECONCILE_REQUIRED"
            )
            self.assertEqual(ready["next_action"]["step_id"], "S2")
            self.assertEqual(reconcile["reconcile_step_id"], "S2")
            self.assertIsNone(reconcile["next_action"])
        finally:
            fx.close()


class CrossProcessResumeCanaryTests(unittest.TestCase):
    def test_killed_worker_resumes_in_fresh_process_without_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_root = root / "state"
            target_root = root / "target"
            target_root.mkdir()
            target_file = target_root / "README.md"
            target_file.write_text("immutable target\n", encoding="utf-8")
            before = hashlib.sha256(target_file.read_bytes()).hexdigest()

            producer = textwrap.dedent(
                r"""
                import json, os, sys, time
                from pathlib import Path
                from nexus_workbench import (
                    ActionCell, ActionMode, ArtifactRecord, JsonWorkbenchStore,
                    ObservationBundle, ObservationOutcome, ResumeCheckpoint,
                    ResumeCoordinator, ResumePhase, WorkbenchNotebook,
                )

                state_root = Path(sys.argv[1])
                store = JsonWorkbenchStore(state_root)
                session = "wb-kill"
                action1 = ActionCell(session, "S1", 1, "read S1", ActionMode.READ_ONLY_PROBE, ("repo.read",))
                obs1 = ObservationBundle(
                    session, "S1", action1.content_hash, "worker-A-executor",
                    "2026-10-06T12:00:00Z", "2026-10-06T12:00:01Z",
                    ObservationOutcome.SUCCEEDED,
                )
                artifact = ArtifactRecord(session, "A1", "application/json", {"producer":"worker-A"})
                notebook = WorkbenchNotebook(
                    workbench_session_id=session,
                    task_id="task-kill",
                    operation_id="op-kill",
                    attempt_id="attempt-kill",
                    target_identity="target-kill",
                    goal="survive process death",
                    current_objective="resume elsewhere",
                    next_probe="S2",
                    observation_refs=("S1",),
                    artifact_refs=("A1",),
                    revision=1,
                )
                action2 = ActionCell(session, "S2", 1, "read S2", ActionMode.READ_ONLY_PROBE, ("repo.read",))
                store.save_action(action1)
                store.save_observation(obs1)
                store.save_artifact(artifact)
                store.save_notebook(notebook)
                checkpoint = ResumeCheckpoint(
                    checkpoint_id="cp-1",
                    workbench_session_id=session,
                    task_id="task-kill",
                    operation_id="op-kill",
                    attempt_id="attempt-kill",
                    target_identity="target-kill",
                    sequence=1,
                    notebook_revision=1,
                    notebook_hash=notebook.content_hash,
                    phase=ResumePhase.READY_FOR_ACTION,
                    completed_step_ids=("S1",),
                    observation_refs=("S1",),
                    artifact_refs=("A1",),
                    next_action=action2,
                )
                ResumeCoordinator(store).publish(checkpoint)
                print(json.dumps({"pid":os.getpid(),"worker":"worker-A","model":"model-A"}), flush=True)
                time.sleep(60)
                """
            )
            process = subprocess.Popen(
                [sys.executable, "-c", producer, str(state_root)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=os.environ.copy(),
            )
            assert process.stdout is not None
            ready = json.loads(process.stdout.readline())
            process.terminate()
            process.communicate(timeout=10)
            self.assertNotEqual(process.returncode, 0)

            consumer = textwrap.dedent(
                r"""
                import json, os, sys
                from pathlib import Path
                from nexus_workbench import JsonWorkbenchStore, ResumeCoordinator, ResumeRequest

                store = JsonWorkbenchStore(Path(sys.argv[1]))
                receipt = ResumeCoordinator(store).resume(
                    ResumeRequest(
                        workbench_session_id="wb-kill",
                        task_id="task-kill",
                        operation_id="op-kill",
                        attempt_id="attempt-kill",
                        target_identity="target-kill",
                        resumer_identity="worker-B",
                        resumer_model_identity="model-B",
                    )
                )
                print(json.dumps({"pid":os.getpid(), **receipt.to_dict()}, sort_keys=True))
                """
            )
            result = subprocess.run(
                [sys.executable, "-c", consumer, str(state_root)],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=os.environ.copy(),
            )
            resumed = json.loads(result.stdout)
            self.assertNotEqual(resumed["pid"], ready["pid"])
            self.assertEqual(resumed["resumer_identity"], "worker-B")
            self.assertEqual(resumed["resumer_model_identity"], "model-B")
            self.assertEqual(resumed["disposition"], "READY_FOR_ACTION")
            self.assertEqual(resumed["next_action"]["step_id"], "S2")
            raw = json.dumps(resumed).lower()
            self.assertNotIn("transcript", raw)
            self.assertNotIn("provider_session", raw)
            self.assertNotIn("model-a", raw)

            after = hashlib.sha256(target_file.read_bytes()).hexdigest()
            self.assertEqual(after, before)
            self.assertEqual(target_file.read_text(encoding="utf-8"), "immutable target\n")


if __name__ == "__main__":
    unittest.main()
