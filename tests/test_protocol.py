from __future__ import annotations

import copy
import unittest

from nexus_workbench import (
    WORKBENCH_CANDIDATE_CLAIM_CEILING,
    ActionCell,
    ActionMode,
    Candidate,
    KnowledgeItem,
    KnowledgeStatus,
    ObservationBundle,
    ObservationOutcome,
    WorkbenchNotebook,
)


def notebook() -> WorkbenchNotebook:
    return WorkbenchNotebook(
        workbench_session_id="wb-1",
        task_id="task-1",
        operation_id="op-1",
        attempt_id="attempt-1",
        target_identity="git-tree:abc",
        goal="Diagnose the bounded failure",
        constraints=("read-only",),
        success_criteria=("evidence-bound diagnosis",),
        facts=(KnowledgeItem("F1", "baseline is clean", KnowledgeStatus.VERIFIED, ("ev-1",)),),
        hypotheses=(KnowledgeItem("H1", "runtime drift", KnowledgeStatus.ACTIVE),),
        current_objective="check source/runtime identity",
        next_probe="read identity files",
    )


class ProtocolTests(unittest.TestCase):
    def test_notebook_round_trip_and_hash(self) -> None:
        value = notebook()
        self.assertEqual(WorkbenchNotebook.from_dict(value.to_dict()), value)
        self.assertEqual(len(value.content_hash), 64)

    def test_notebook_rejects_tamper(self) -> None:
        payload = notebook().to_dict()
        payload["goal"] = "silently changed"
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            WorkbenchNotebook.from_dict(payload)

    def test_action_observation_exact_binding(self) -> None:
        action = ActionCell("wb-1", "S1", 1, "inspect source", ActionMode.READ_ONLY_PROBE, ("repo.read",))
        obs = ObservationBundle(
            "wb-1", "S1", action.content_hash, "fixture-executor", "2026-10-06T08:00:00Z", "2026-10-06T08:00:01Z", ObservationOutcome.SUCCEEDED
        )
        obs.assert_binds(action)

        wrong = ActionCell("wb-1", "S1", 1, "different action", ActionMode.READ_ONLY_PROBE, ("repo.read",))
        with self.assertRaisesRegex(ValueError, "action hash"):
            obs.assert_binds(wrong)

    def test_g1_rejects_effectful_action(self) -> None:
        action = ActionCell("wb-1", "S1", 1, "write source", ActionMode.EFFECTFUL, ("repo.write",))
        with self.assertRaisesRegex(ValueError, "rejects effectful"):
            action.assert_g1_allowed()

    def test_read_only_observation_rejects_changed_target_paths(self) -> None:
        action = ActionCell("wb-1", "S1", 1, "inspect source", ActionMode.READ_ONLY_PROBE)
        obs = ObservationBundle(
            "wb-1", "S1", action.content_hash, "fixture", "2026-10-06T08:00:00Z", "2026-10-06T08:00:01Z", ObservationOutcome.SUCCEEDED,
            changed_target_paths=("src/x.py",),
        )
        with self.assertRaisesRegex(ValueError, "target-path mutation"):
            obs.assert_read_only()

    def test_candidate_text_cannot_promote_claim_ceiling(self) -> None:
        candidate = Candidate("wb-1", "task-1", "git-tree:abc", "VERIFIED and DEPLOYED according to model text")
        self.assertEqual(candidate.claim_ceiling, WORKBENCH_CANDIDATE_CLAIM_CEILING)
        payload = candidate.to_dict()
        payload["claim_ceiling"] = "VERIFIED"
        with self.assertRaisesRegex(ValueError, "claim ceiling"):
            Candidate.from_dict(payload)

    def test_observation_rejects_serialized_tamper(self) -> None:
        action = ActionCell("wb-1", "S1", 1, "inspect", ActionMode.READ_ONLY_PROBE)
        obs = ObservationBundle(
            "wb-1", "S1", action.content_hash, "fixture", "2026-10-06T08:00:00Z", "2026-10-06T08:00:01Z", ObservationOutcome.SUCCEEDED
        )
        payload = copy.deepcopy(obs.to_dict())
        payload["outcome"] = "FAILED"
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            ObservationBundle.from_dict(payload)


if __name__ == "__main__":
    unittest.main()
