from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping, Sequence

NOTEBOOK_SCHEMA = "nexus.workbench.notebook.v1"
ACTION_SCHEMA = "nexus.workbench.action_cell.v1"
OBSERVATION_SCHEMA = "nexus.workbench.observation_bundle.v1"
CANDIDATE_SCHEMA = "nexus.workbench.candidate.v1"
WORKBENCH_REASONING_CLAIM_CEILING = "WORKBENCH_REASONING_STATE_ONLY"
WORKBENCH_CANDIDATE_CLAIM_CEILING = "WORKBENCH_CANDIDATE_ONLY"


def _text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _strings(values: Sequence[str], field_name: str) -> tuple[str, ...]:
    normalized = tuple(_text(value, field_name) for value in values)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field_name} must not contain duplicates")
    return normalized


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


class KnowledgeStatus(StrEnum):
    VERIFIED = "VERIFIED"
    CONTRADICTED = "CONTRADICTED"
    ACTIVE = "ACTIVE"
    UNTESTED = "UNTESTED"
    UNKNOWN = "UNKNOWN"


class ActionMode(StrEnum):
    PURE_COMPUTE = "PURE_COMPUTE"
    READ_ONLY_PROBE = "READ_ONLY_PROBE"
    EFFECTFUL = "EFFECTFUL"


class ObservationOutcome(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
    BLOCKED = "BLOCKED"


G1_ALLOWED_ACTION_MODES = frozenset({ActionMode.PURE_COMPUTE, ActionMode.READ_ONLY_PROBE})


@dataclass(frozen=True)
class KnowledgeItem:
    item_id: str
    statement: str
    status: KnowledgeStatus
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "item_id", _text(self.item_id, "item_id"))
        object.__setattr__(self, "statement", _text(self.statement, "statement"))
        object.__setattr__(self, "evidence_refs", _strings(self.evidence_refs, "evidence_refs"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "statement": self.statement,
            "status": self.status.value,
            "evidence_refs": list(self.evidence_refs),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "KnowledgeItem":
        return cls(
            item_id=_text(data.get("item_id"), "item_id"),
            statement=_text(data.get("statement"), "statement"),
            status=KnowledgeStatus(_text(data.get("status"), "status")),
            evidence_refs=tuple(data.get("evidence_refs") or ()),
        )


@dataclass(frozen=True)
class WorkbenchNotebook:
    workbench_session_id: str
    task_id: str
    operation_id: str
    attempt_id: str
    target_identity: str
    goal: str
    constraints: tuple[str, ...] = ()
    success_criteria: tuple[str, ...] = ()
    facts: tuple[KnowledgeItem, ...] = ()
    hypotheses: tuple[KnowledgeItem, ...] = ()
    current_objective: str = ""
    next_probe: str = ""
    last_material_delta: str = ""
    no_progress_count: int = 0
    unresolved: tuple[str, ...] = ()
    observation_refs: tuple[str, ...] = ()
    artifact_refs: tuple[str, ...] = ()
    revision: int = 1
    schema: str = NOTEBOOK_SCHEMA
    claim_ceiling: str = WORKBENCH_REASONING_CLAIM_CEILING

    def __post_init__(self) -> None:
        for name in ("workbench_session_id", "task_id", "operation_id", "attempt_id", "target_identity", "goal"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        if self.schema != NOTEBOOK_SCHEMA:
            raise ValueError("unsupported notebook schema")
        if self.claim_ceiling != WORKBENCH_REASONING_CLAIM_CEILING:
            raise ValueError("notebook claim ceiling cannot be widened")
        if isinstance(self.revision, bool) or self.revision < 1:
            raise ValueError("revision must be an integer >= 1")
        if isinstance(self.no_progress_count, bool) or self.no_progress_count < 0:
            raise ValueError("no_progress_count must be an integer >= 0")
        for name in ("constraints", "success_criteria", "unresolved", "observation_refs", "artifact_refs"):
            object.__setattr__(self, name, _strings(getattr(self, name), name))
        ids = [item.item_id for item in self.facts + self.hypotheses]
        if len(ids) != len(set(ids)):
            raise ValueError("fact/hypothesis IDs must be unique within a notebook")

    def _body(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "workbench_session_id": self.workbench_session_id,
            "task_id": self.task_id,
            "operation_id": self.operation_id,
            "attempt_id": self.attempt_id,
            "target_identity": self.target_identity,
            "revision": self.revision,
            "goal": self.goal,
            "constraints": list(self.constraints),
            "success_criteria": list(self.success_criteria),
            "facts": [item.to_dict() for item in self.facts],
            "hypotheses": [item.to_dict() for item in self.hypotheses],
            "plan": {"current_objective": self.current_objective, "next_probe": self.next_probe},
            "progress": {
                "last_material_delta": self.last_material_delta,
                "no_progress_count": self.no_progress_count,
            },
            "unresolved": list(self.unresolved),
            "observation_refs": list(self.observation_refs),
            "artifact_refs": list(self.artifact_refs),
            "claim_ceiling": self.claim_ceiling,
        }

    @property
    def content_hash(self) -> str:
        return _sha256(self._body())

    def to_dict(self) -> dict[str, Any]:
        return {**self._body(), "content_hash": self.content_hash}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "WorkbenchNotebook":
        plan = data.get("plan") or {}
        progress = data.get("progress") or {}
        notebook = cls(
            schema=_text(data.get("schema"), "schema"),
            workbench_session_id=_text(data.get("workbench_session_id"), "workbench_session_id"),
            task_id=_text(data.get("task_id"), "task_id"),
            operation_id=_text(data.get("operation_id"), "operation_id"),
            attempt_id=_text(data.get("attempt_id"), "attempt_id"),
            target_identity=_text(data.get("target_identity"), "target_identity"),
            revision=data.get("revision", 1),
            goal=_text(data.get("goal"), "goal"),
            constraints=tuple(data.get("constraints") or ()),
            success_criteria=tuple(data.get("success_criteria") or ()),
            facts=tuple(KnowledgeItem.from_dict(x) for x in data.get("facts") or ()),
            hypotheses=tuple(KnowledgeItem.from_dict(x) for x in data.get("hypotheses") or ()),
            current_objective=str(plan.get("current_objective") or ""),
            next_probe=str(plan.get("next_probe") or ""),
            last_material_delta=str(progress.get("last_material_delta") or ""),
            no_progress_count=progress.get("no_progress_count", 0),
            unresolved=tuple(data.get("unresolved") or ()),
            observation_refs=tuple(data.get("observation_refs") or ()),
            artifact_refs=tuple(data.get("artifact_refs") or ()),
            claim_ceiling=_text(data.get("claim_ceiling"), "claim_ceiling"),
        )
        stored_hash = _text(data.get("content_hash"), "content_hash")
        if stored_hash != notebook.content_hash:
            raise ValueError("notebook content hash mismatch")
        return notebook


@dataclass(frozen=True)
class ActionCell:
    workbench_session_id: str
    step_id: str
    notebook_revision: int
    intent: str
    mode: ActionMode
    requested_capabilities: tuple[str, ...] = ()
    input_refs: tuple[str, ...] = ()
    code_or_action: str = ""
    schema: str = ACTION_SCHEMA

    def __post_init__(self) -> None:
        object.__setattr__(self, "workbench_session_id", _text(self.workbench_session_id, "workbench_session_id"))
        object.__setattr__(self, "step_id", _text(self.step_id, "step_id"))
        object.__setattr__(self, "intent", _text(self.intent, "intent"))
        if self.schema != ACTION_SCHEMA:
            raise ValueError("unsupported action schema")
        if isinstance(self.notebook_revision, bool) or self.notebook_revision < 1:
            raise ValueError("notebook_revision must be an integer >= 1")
        object.__setattr__(self, "requested_capabilities", _strings(self.requested_capabilities, "requested_capabilities"))
        object.__setattr__(self, "input_refs", _strings(self.input_refs, "input_refs"))

    def _body(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "workbench_session_id": self.workbench_session_id,
            "step_id": self.step_id,
            "notebook_revision": self.notebook_revision,
            "intent": self.intent,
            "mode": self.mode.value,
            "requested_capabilities": list(self.requested_capabilities),
            "input_refs": list(self.input_refs),
            "code_or_action": self.code_or_action,
        }

    @property
    def content_hash(self) -> str:
        return _sha256(self._body())

    def to_dict(self) -> dict[str, Any]:
        return {**self._body(), "content_hash": self.content_hash}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ActionCell":
        action = cls(
            schema=_text(data.get("schema"), "schema"),
            workbench_session_id=_text(data.get("workbench_session_id"), "workbench_session_id"),
            step_id=_text(data.get("step_id"), "step_id"),
            notebook_revision=data.get("notebook_revision", 0),
            intent=_text(data.get("intent"), "intent"),
            mode=ActionMode(_text(data.get("mode"), "mode")),
            requested_capabilities=tuple(data.get("requested_capabilities") or ()),
            input_refs=tuple(data.get("input_refs") or ()),
            code_or_action=str(data.get("code_or_action") or ""),
        )
        if _text(data.get("content_hash"), "content_hash") != action.content_hash:
            raise ValueError("action content hash mismatch")
        return action

    def assert_g1_allowed(self) -> None:
        if self.mode not in G1_ALLOWED_ACTION_MODES:
            raise ValueError("G1 rejects effectful Action Cells")


@dataclass(frozen=True)
class ObservationBundle:
    workbench_session_id: str
    step_id: str
    action_hash: str
    executor_identity: str
    started_at: str
    finished_at: str
    outcome: ObservationOutcome
    stdout_ref: str = ""
    stderr_ref: str = ""
    result_summary: str = ""
    physical_readback: Mapping[str, Any] = field(default_factory=dict)
    artifact_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    changed_target_paths: tuple[str, ...] = ()
    schema: str = OBSERVATION_SCHEMA

    def __post_init__(self) -> None:
        for name in ("workbench_session_id", "step_id", "action_hash", "executor_identity", "started_at", "finished_at"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        if self.schema != OBSERVATION_SCHEMA:
            raise ValueError("unsupported observation schema")
        for name in ("artifact_refs", "evidence_refs", "changed_target_paths"):
            object.__setattr__(self, name, _strings(getattr(self, name), name))
        object.__setattr__(self, "physical_readback", dict(self.physical_readback))

    def _body(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "workbench_session_id": self.workbench_session_id,
            "step_id": self.step_id,
            "action_hash": self.action_hash,
            "executor_identity": self.executor_identity,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "outcome": self.outcome.value,
            "stdout_ref": self.stdout_ref,
            "stderr_ref": self.stderr_ref,
            "result_summary": self.result_summary,
            "physical_readback": dict(self.physical_readback),
            "artifact_refs": list(self.artifact_refs),
            "evidence_refs": list(self.evidence_refs),
            "changed_target_paths": list(self.changed_target_paths),
        }

    @property
    def observation_hash(self) -> str:
        return _sha256(self._body())

    def to_dict(self) -> dict[str, Any]:
        return {**self._body(), "observation_hash": self.observation_hash}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ObservationBundle":
        observation = cls(
            schema=_text(data.get("schema"), "schema"),
            workbench_session_id=_text(data.get("workbench_session_id"), "workbench_session_id"),
            step_id=_text(data.get("step_id"), "step_id"),
            action_hash=_text(data.get("action_hash"), "action_hash"),
            executor_identity=_text(data.get("executor_identity"), "executor_identity"),
            started_at=_text(data.get("started_at"), "started_at"),
            finished_at=_text(data.get("finished_at"), "finished_at"),
            outcome=ObservationOutcome(_text(data.get("outcome"), "outcome")),
            stdout_ref=str(data.get("stdout_ref") or ""),
            stderr_ref=str(data.get("stderr_ref") or ""),
            result_summary=str(data.get("result_summary") or ""),
            physical_readback=data.get("physical_readback") or {},
            artifact_refs=tuple(data.get("artifact_refs") or ()),
            evidence_refs=tuple(data.get("evidence_refs") or ()),
            changed_target_paths=tuple(data.get("changed_target_paths") or ()),
        )
        if _text(data.get("observation_hash"), "observation_hash") != observation.observation_hash:
            raise ValueError("observation hash mismatch")
        return observation

    def assert_binds(self, action: ActionCell) -> None:
        if self.workbench_session_id != action.workbench_session_id:
            raise ValueError("observation session does not match Action Cell")
        if self.step_id != action.step_id:
            raise ValueError("observation step does not match Action Cell")
        if self.action_hash != action.content_hash:
            raise ValueError("observation action hash does not match Action Cell")

    def assert_read_only(self) -> None:
        if self.changed_target_paths:
            raise ValueError("read-only observation reports target-path mutation")


@dataclass(frozen=True)
class Candidate:
    workbench_session_id: str
    task_id: str
    target_identity: str
    summary: str
    evidence_refs: tuple[str, ...] = ()
    artifact_refs: tuple[str, ...] = ()
    schema: str = CANDIDATE_SCHEMA
    claim_ceiling: str = WORKBENCH_CANDIDATE_CLAIM_CEILING

    def __post_init__(self) -> None:
        for name in ("workbench_session_id", "task_id", "target_identity", "summary"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        if self.schema != CANDIDATE_SCHEMA:
            raise ValueError("unsupported candidate schema")
        if self.claim_ceiling != WORKBENCH_CANDIDATE_CLAIM_CEILING:
            raise ValueError("candidate claim ceiling cannot be widened")
        object.__setattr__(self, "evidence_refs", _strings(self.evidence_refs, "evidence_refs"))
        object.__setattr__(self, "artifact_refs", _strings(self.artifact_refs, "artifact_refs"))

    def _body(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "workbench_session_id": self.workbench_session_id,
            "task_id": self.task_id,
            "target_identity": self.target_identity,
            "summary": self.summary,
            "evidence_refs": list(self.evidence_refs),
            "artifact_refs": list(self.artifact_refs),
            "claim_ceiling": self.claim_ceiling,
        }

    @property
    def candidate_hash(self) -> str:
        return _sha256(self._body())

    def to_dict(self) -> dict[str, Any]:
        return {**self._body(), "candidate_hash": self.candidate_hash}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Candidate":
        candidate = cls(
            schema=_text(data.get("schema"), "schema"),
            workbench_session_id=_text(data.get("workbench_session_id"), "workbench_session_id"),
            task_id=_text(data.get("task_id"), "task_id"),
            target_identity=_text(data.get("target_identity"), "target_identity"),
            summary=_text(data.get("summary"), "summary"),
            evidence_refs=tuple(data.get("evidence_refs") or ()),
            artifact_refs=tuple(data.get("artifact_refs") or ()),
            claim_ceiling=_text(data.get("claim_ceiling"), "claim_ceiling"),
        )
        if _text(data.get("candidate_hash"), "candidate_hash") != candidate.candidate_hash:
            raise ValueError("candidate hash mismatch")
        return candidate
