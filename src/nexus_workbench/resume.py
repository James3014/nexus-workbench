from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import hashlib
import json
import re
from typing import Any, Mapping

from .model import ActionCell, ObservationOutcome, WorkbenchNotebook

ARTIFACT_SCHEMA = "nexus.workbench.artifact_record.v1"
RESUME_CHECKPOINT_SCHEMA = "nexus.workbench.resume_checkpoint.v1"
RESUME_HEAD_SCHEMA = "nexus.workbench.resume_head.v1"
RESUME_RECEIPT_SCHEMA = "nexus.workbench.resume_receipt.v1"

WORKBENCH_RESUME_CLAIM_CEILING = "WORKBENCH_RESUME_STATE_ONLY"
G4_CLAIM_CEILING = "G4_DURABLE_RESUME_CANARY_ONLY"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ResumePhase(StrEnum):
    READY_FOR_ACTION = "READY_FOR_ACTION"
    IN_FLIGHT = "IN_FLIGHT"
    RECONCILE_REQUIRED = "RECONCILE_REQUIRED"
    COMPLETE = "COMPLETE"


class ResumeDisposition(StrEnum):
    READY_FOR_ACTION = "READY_FOR_ACTION"
    RECONCILE_REQUIRED = "RECONCILE_REQUIRED"
    COMPLETE = "COMPLETE"


class ResumeBlocked(RuntimeError):
    """Fail-closed G4 resume error with a stable reason code."""

    def __init__(self, code: str, message: str):
        self.code = _text(code, "code")
        super().__init__(f"{self.code}: {message}")


def _text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _optional_text(value: Any, field_name: str) -> str:
    if value in (None, ""):
        return ""
    return _text(value, field_name)


def _sha256_text(value: Any, field_name: str) -> str:
    text = _text(value, field_name).lower()
    if not _SHA256_RE.fullmatch(text):
        raise ValueError(f"{field_name} must be a lowercase sha256 hex digest")
    return text


def _strings(values: Any, field_name: str) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, str) or not isinstance(values, (list, tuple)):
        raise ValueError(f"{field_name} must be a list/tuple of strings")
    normalized = tuple(_text(value, field_name) for value in values)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field_name} must not contain duplicates")
    return normalized


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _json_mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a mapping")
    try:
        raw = _canonical(dict(value))
        normalized = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be JSON-serializable") from exc
    if not isinstance(normalized, dict):
        raise ValueError(f"{field_name} must be a JSON object")
    return normalized


def _exact_keys(data: Mapping[str, Any], expected: set[str], label: str) -> None:
    actual = set(data)
    if actual != expected:
        raise ValueError(
            f"{label} keys mismatch; extra={sorted(actual - expected)}, missing={sorted(expected - actual)}"
        )


@dataclass(frozen=True)
class ArtifactRecord:
    workbench_session_id: str
    artifact_id: str
    media_type: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    schema: str = ARTIFACT_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != ARTIFACT_SCHEMA:
            raise ValueError("unsupported artifact schema")
        object.__setattr__(
            self,
            "workbench_session_id",
            _text(self.workbench_session_id, "workbench_session_id"),
        )
        object.__setattr__(self, "artifact_id", _text(self.artifact_id, "artifact_id"))
        object.__setattr__(self, "media_type", _text(self.media_type, "media_type"))
        object.__setattr__(self, "payload", _json_mapping(self.payload, "payload"))

    def _body(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "workbench_session_id": self.workbench_session_id,
            "artifact_id": self.artifact_id,
            "media_type": self.media_type,
            "payload": dict(self.payload),
        }

    @property
    def artifact_hash(self) -> str:
        return _sha256(self._body())

    def to_dict(self) -> dict[str, Any]:
        return {**self._body(), "artifact_hash": self.artifact_hash}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ArtifactRecord":
        _exact_keys(
            data,
            {
                "schema",
                "workbench_session_id",
                "artifact_id",
                "media_type",
                "payload",
                "artifact_hash",
            },
            "artifact record",
        )
        artifact = cls(
            schema=_text(data.get("schema"), "schema"),
            workbench_session_id=_text(
                data.get("workbench_session_id"), "workbench_session_id"
            ),
            artifact_id=_text(data.get("artifact_id"), "artifact_id"),
            media_type=_text(data.get("media_type"), "media_type"),
            payload=data.get("payload") or {},
        )
        if _sha256_text(data.get("artifact_hash"), "artifact_hash") != artifact.artifact_hash:
            raise ValueError("artifact hash mismatch")
        return artifact


@dataclass(frozen=True)
class ResumeCheckpoint:
    checkpoint_id: str
    workbench_session_id: str
    task_id: str
    operation_id: str
    attempt_id: str
    target_identity: str
    sequence: int
    notebook_revision: int
    notebook_hash: str
    phase: ResumePhase
    completed_step_ids: tuple[str, ...] = ()
    observation_refs: tuple[str, ...] = ()
    artifact_refs: tuple[str, ...] = ()
    previous_checkpoint_id: str = ""
    previous_checkpoint_hash: str = ""
    next_action: ActionCell | None = None
    active_action: ActionCell | None = None
    schema: str = RESUME_CHECKPOINT_SCHEMA
    claim_ceiling: str = WORKBENCH_RESUME_CLAIM_CEILING

    def __post_init__(self) -> None:
        if self.schema != RESUME_CHECKPOINT_SCHEMA:
            raise ValueError("unsupported resume checkpoint schema")
        if self.claim_ceiling != WORKBENCH_RESUME_CLAIM_CEILING:
            raise ValueError("resume checkpoint claim ceiling cannot be widened")
        for name in (
            "checkpoint_id",
            "workbench_session_id",
            "task_id",
            "operation_id",
            "attempt_id",
            "target_identity",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int) or self.sequence < 1:
            raise ValueError("sequence must be an integer >= 1")
        if (
            isinstance(self.notebook_revision, bool)
            or not isinstance(self.notebook_revision, int)
            or self.notebook_revision < 1
        ):
            raise ValueError("notebook_revision must be an integer >= 1")
        object.__setattr__(
            self, "notebook_hash", _sha256_text(self.notebook_hash, "notebook_hash")
        )
        object.__setattr__(
            self,
            "completed_step_ids",
            _strings(self.completed_step_ids, "completed_step_ids"),
        )
        object.__setattr__(
            self,
            "observation_refs",
            _strings(self.observation_refs, "observation_refs"),
        )
        object.__setattr__(
            self, "artifact_refs", _strings(self.artifact_refs, "artifact_refs")
        )
        previous_id = _optional_text(self.previous_checkpoint_id, "previous_checkpoint_id")
        previous_hash = _optional_text(
            self.previous_checkpoint_hash, "previous_checkpoint_hash"
        )
        if bool(previous_id) != bool(previous_hash):
            raise ValueError(
                "previous_checkpoint_id and previous_checkpoint_hash must appear together"
            )
        if previous_hash:
            previous_hash = _sha256_text(previous_hash, "previous_checkpoint_hash")
        if self.sequence == 1 and (previous_id or previous_hash):
            raise ValueError("first checkpoint must not reference a previous checkpoint")
        if self.sequence > 1 and not previous_id:
            raise ValueError("checkpoint sequence > 1 requires previous checkpoint binding")
        object.__setattr__(self, "previous_checkpoint_id", previous_id)
        object.__setattr__(self, "previous_checkpoint_hash", previous_hash)

        next_action = self.next_action
        active_action = self.active_action
        if next_action is not None and type(next_action) is not ActionCell:
            raise ValueError("next_action must be ActionCell")
        if active_action is not None and type(active_action) is not ActionCell:
            raise ValueError("active_action must be ActionCell")

        if self.phase is ResumePhase.READY_FOR_ACTION:
            if next_action is None or active_action is not None:
                raise ValueError("READY_FOR_ACTION requires only next_action")
            self._assert_action_identity(next_action, "next_action")
            if next_action.step_id in self.completed_step_ids:
                raise ValueError("next action step is already completed")
        elif self.phase is ResumePhase.IN_FLIGHT:
            if active_action is None or next_action is not None:
                raise ValueError("IN_FLIGHT requires only active_action")
            self._assert_action_identity(active_action, "active_action")
            if active_action.step_id in self.completed_step_ids:
                raise ValueError("active action step is already completed")
        elif self.phase is ResumePhase.RECONCILE_REQUIRED:
            if active_action is None or next_action is not None:
                raise ValueError("RECONCILE_REQUIRED requires only active_action")
            self._assert_action_identity(active_action, "active_action")
            if active_action.step_id in self.completed_step_ids:
                raise ValueError("reconcile action step is already completed")
        elif self.phase is ResumePhase.COMPLETE:
            if next_action is not None or active_action is not None:
                raise ValueError("COMPLETE must not contain an action")
        else:  # pragma: no cover - enum construction prevents this
            raise ValueError("unsupported resume phase")

    def _assert_action_identity(self, action: ActionCell, label: str) -> None:
        if action.workbench_session_id != self.workbench_session_id:
            raise ValueError(f"{label} session does not match checkpoint")
        if action.notebook_revision != self.notebook_revision:
            raise ValueError(f"{label} notebook revision does not match checkpoint")

    def _body(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "checkpoint_id": self.checkpoint_id,
            "workbench_session_id": self.workbench_session_id,
            "task_id": self.task_id,
            "operation_id": self.operation_id,
            "attempt_id": self.attempt_id,
            "target_identity": self.target_identity,
            "sequence": self.sequence,
            "previous_checkpoint_id": self.previous_checkpoint_id,
            "previous_checkpoint_hash": self.previous_checkpoint_hash,
            "notebook_revision": self.notebook_revision,
            "notebook_hash": self.notebook_hash,
            "phase": self.phase.value,
            "completed_step_ids": list(self.completed_step_ids),
            "observation_refs": list(self.observation_refs),
            "artifact_refs": list(self.artifact_refs),
            "next_action": self.next_action.to_dict() if self.next_action else None,
            "active_action": self.active_action.to_dict() if self.active_action else None,
            "claim_ceiling": self.claim_ceiling,
        }

    @property
    def checkpoint_hash(self) -> str:
        return _sha256(self._body())

    def to_dict(self) -> dict[str, Any]:
        return {**self._body(), "checkpoint_hash": self.checkpoint_hash}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ResumeCheckpoint":
        _exact_keys(
            data,
            {
                "schema",
                "checkpoint_id",
                "workbench_session_id",
                "task_id",
                "operation_id",
                "attempt_id",
                "target_identity",
                "sequence",
                "previous_checkpoint_id",
                "previous_checkpoint_hash",
                "notebook_revision",
                "notebook_hash",
                "phase",
                "completed_step_ids",
                "observation_refs",
                "artifact_refs",
                "next_action",
                "active_action",
                "claim_ceiling",
                "checkpoint_hash",
            },
            "resume checkpoint",
        )
        checkpoint = cls(
            schema=_text(data.get("schema"), "schema"),
            checkpoint_id=_text(data.get("checkpoint_id"), "checkpoint_id"),
            workbench_session_id=_text(
                data.get("workbench_session_id"), "workbench_session_id"
            ),
            task_id=_text(data.get("task_id"), "task_id"),
            operation_id=_text(data.get("operation_id"), "operation_id"),
            attempt_id=_text(data.get("attempt_id"), "attempt_id"),
            target_identity=_text(data.get("target_identity"), "target_identity"),
            sequence=data.get("sequence"),
            previous_checkpoint_id=str(data.get("previous_checkpoint_id") or ""),
            previous_checkpoint_hash=str(data.get("previous_checkpoint_hash") or ""),
            notebook_revision=data.get("notebook_revision"),
            notebook_hash=_text(data.get("notebook_hash"), "notebook_hash"),
            phase=ResumePhase(_text(data.get("phase"), "phase")),
            completed_step_ids=tuple(data.get("completed_step_ids") or ()),
            observation_refs=tuple(data.get("observation_refs") or ()),
            artifact_refs=tuple(data.get("artifact_refs") or ()),
            next_action=(
                ActionCell.from_dict(data["next_action"])
                if data.get("next_action") is not None
                else None
            ),
            active_action=(
                ActionCell.from_dict(data["active_action"])
                if data.get("active_action") is not None
                else None
            ),
            claim_ceiling=_text(data.get("claim_ceiling"), "claim_ceiling"),
        )
        if (
            _sha256_text(data.get("checkpoint_hash"), "checkpoint_hash")
            != checkpoint.checkpoint_hash
        ):
            raise ValueError("resume checkpoint hash mismatch")
        return checkpoint


@dataclass(frozen=True)
class ResumeHead:
    workbench_session_id: str
    checkpoint_id: str
    checkpoint_hash: str
    sequence: int
    schema: str = RESUME_HEAD_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != RESUME_HEAD_SCHEMA:
            raise ValueError("unsupported resume head schema")
        object.__setattr__(
            self,
            "workbench_session_id",
            _text(self.workbench_session_id, "workbench_session_id"),
        )
        object.__setattr__(self, "checkpoint_id", _text(self.checkpoint_id, "checkpoint_id"))
        object.__setattr__(
            self,
            "checkpoint_hash",
            _sha256_text(self.checkpoint_hash, "checkpoint_hash"),
        )
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int) or self.sequence < 1:
            raise ValueError("resume head sequence must be an integer >= 1")

    def _body(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "workbench_session_id": self.workbench_session_id,
            "checkpoint_id": self.checkpoint_id,
            "checkpoint_hash": self.checkpoint_hash,
            "sequence": self.sequence,
        }

    @property
    def content_hash(self) -> str:
        return _sha256(self._body())

    def to_dict(self) -> dict[str, Any]:
        return {**self._body(), "content_hash": self.content_hash}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ResumeHead":
        _exact_keys(
            data,
            {
                "schema",
                "workbench_session_id",
                "checkpoint_id",
                "checkpoint_hash",
                "sequence",
                "content_hash",
            },
            "resume head",
        )
        head = cls(
            schema=_text(data.get("schema"), "schema"),
            workbench_session_id=_text(
                data.get("workbench_session_id"), "workbench_session_id"
            ),
            checkpoint_id=_text(data.get("checkpoint_id"), "checkpoint_id"),
            checkpoint_hash=_text(data.get("checkpoint_hash"), "checkpoint_hash"),
            sequence=data.get("sequence"),
        )
        if _sha256_text(data.get("content_hash"), "content_hash") != head.content_hash:
            raise ValueError("resume head content hash mismatch")
        return head


@dataclass(frozen=True)
class ResumeRequest:
    workbench_session_id: str
    task_id: str
    operation_id: str
    attempt_id: str
    target_identity: str
    resumer_identity: str
    resumer_model_identity: str

    def __post_init__(self) -> None:
        for name in (
            "workbench_session_id",
            "task_id",
            "operation_id",
            "attempt_id",
            "target_identity",
            "resumer_identity",
            "resumer_model_identity",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))


@dataclass(frozen=True)
class ResumeReceipt:
    workbench_session_id: str
    task_id: str
    operation_id: str
    attempt_id: str
    target_identity: str
    checkpoint_id: str
    checkpoint_hash: str
    notebook_hash: str
    disposition: ResumeDisposition
    resumer_identity: str
    resumer_model_identity: str
    next_action: ActionCell | None = None
    reconcile_step_id: str = ""
    schema: str = RESUME_RECEIPT_SCHEMA
    claim_ceiling: str = G4_CLAIM_CEILING

    def __post_init__(self) -> None:
        if self.schema != RESUME_RECEIPT_SCHEMA:
            raise ValueError("unsupported resume receipt schema")
        if self.claim_ceiling != G4_CLAIM_CEILING:
            raise ValueError("resume receipt claim ceiling cannot be widened")
        for name in (
            "workbench_session_id",
            "task_id",
            "operation_id",
            "attempt_id",
            "target_identity",
            "checkpoint_id",
            "resumer_identity",
            "resumer_model_identity",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        object.__setattr__(
            self,
            "checkpoint_hash",
            _sha256_text(self.checkpoint_hash, "checkpoint_hash"),
        )
        object.__setattr__(
            self, "notebook_hash", _sha256_text(self.notebook_hash, "notebook_hash")
        )
        reconcile = _optional_text(self.reconcile_step_id, "reconcile_step_id")
        object.__setattr__(self, "reconcile_step_id", reconcile)

        if self.disposition is ResumeDisposition.READY_FOR_ACTION:
            if self.next_action is None or reconcile:
                raise ValueError("READY_FOR_ACTION receipt requires only next_action")
        elif self.disposition is ResumeDisposition.RECONCILE_REQUIRED:
            if self.next_action is not None or not reconcile:
                raise ValueError(
                    "RECONCILE_REQUIRED receipt requires reconcile_step_id"
                )
        elif self.disposition is ResumeDisposition.COMPLETE:
            if self.next_action is not None or reconcile:
                raise ValueError("COMPLETE receipt must not contain continuation work")

    def _body(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "workbench_session_id": self.workbench_session_id,
            "task_id": self.task_id,
            "operation_id": self.operation_id,
            "attempt_id": self.attempt_id,
            "target_identity": self.target_identity,
            "checkpoint_id": self.checkpoint_id,
            "checkpoint_hash": self.checkpoint_hash,
            "notebook_hash": self.notebook_hash,
            "disposition": self.disposition.value,
            "resumer_identity": self.resumer_identity,
            "resumer_model_identity": self.resumer_model_identity,
            "next_action": self.next_action.to_dict() if self.next_action else None,
            "reconcile_step_id": self.reconcile_step_id,
            "claim_ceiling": self.claim_ceiling,
        }

    @property
    def receipt_hash(self) -> str:
        return _sha256(self._body())

    def to_dict(self) -> dict[str, Any]:
        return {**self._body(), "receipt_hash": self.receipt_hash}


class ResumeCoordinator:
    """Validates and resumes durable Workbench state without model/chat memory."""

    def __init__(self, store: Any):
        self.store = store

    def publish(self, checkpoint: ResumeCheckpoint) -> Any:
        notebook = self._load_bound_notebook(checkpoint)
        self._validate_references(checkpoint, notebook)

        previous = None
        if checkpoint.sequence > 1:
            previous = self.store.load_resume_checkpoint(
                checkpoint.workbench_session_id, checkpoint.previous_checkpoint_id
            )
            if previous.checkpoint_hash != checkpoint.previous_checkpoint_hash:
                raise ResumeBlocked(
                    "LINEAGE_HASH_MISMATCH",
                    "previous checkpoint hash does not match durable predecessor",
                )
            self._validate_transition(previous, checkpoint)
        return self.store.save_resume_checkpoint(checkpoint)

    def resume(self, request: ResumeRequest) -> ResumeReceipt:
        try:
            head = self.store.load_resume_head(request.workbench_session_id)
            checkpoint = self.store.load_resume_checkpoint(
                request.workbench_session_id, head.checkpoint_id
            )
        except FileNotFoundError as exc:
            raise ResumeBlocked("RESUME_STATE_MISSING", "resume head/checkpoint is missing") from exc

        if head.checkpoint_hash != checkpoint.checkpoint_hash:
            raise ResumeBlocked(
                "HEAD_HASH_MISMATCH", "resume head does not bind the loaded checkpoint"
            )
        if head.sequence != checkpoint.sequence:
            raise ResumeBlocked(
                "HEAD_SEQUENCE_MISMATCH",
                "resume head sequence does not bind the loaded checkpoint",
            )

        self._assert_request_identity(request, checkpoint)
        self._validate_lineage(checkpoint)
        notebook = self._load_bound_notebook(checkpoint)
        self._validate_references(checkpoint, notebook)

        if checkpoint.phase is ResumePhase.READY_FOR_ACTION:
            action = checkpoint.next_action
            assert action is not None
            if self.store.action_exists(
                checkpoint.workbench_session_id, action.step_id
            ) or self.store.observation_exists(
                checkpoint.workbench_session_id, action.step_id
            ):
                raise ResumeBlocked(
                    "NEXT_STEP_ALREADY_MATERIALIZED",
                    "next action step already has durable action/observation state; reconcile instead of replaying",
                )
            return self._receipt(
                request,
                checkpoint,
                notebook,
                ResumeDisposition.READY_FOR_ACTION,
                next_action=action,
            )

        if checkpoint.phase in (
            ResumePhase.IN_FLIGHT,
            ResumePhase.RECONCILE_REQUIRED,
        ):
            action = checkpoint.active_action
            assert action is not None
            self._assert_active_action(checkpoint, action)
            return self._receipt(
                request,
                checkpoint,
                notebook,
                ResumeDisposition.RECONCILE_REQUIRED,
                reconcile_step_id=action.step_id,
            )

        return self._receipt(
            request, checkpoint, notebook, ResumeDisposition.COMPLETE
        )

    def _receipt(
        self,
        request: ResumeRequest,
        checkpoint: ResumeCheckpoint,
        notebook: WorkbenchNotebook,
        disposition: ResumeDisposition,
        *,
        next_action: ActionCell | None = None,
        reconcile_step_id: str = "",
    ) -> ResumeReceipt:
        return ResumeReceipt(
            workbench_session_id=checkpoint.workbench_session_id,
            task_id=checkpoint.task_id,
            operation_id=checkpoint.operation_id,
            attempt_id=checkpoint.attempt_id,
            target_identity=checkpoint.target_identity,
            checkpoint_id=checkpoint.checkpoint_id,
            checkpoint_hash=checkpoint.checkpoint_hash,
            notebook_hash=notebook.content_hash,
            disposition=disposition,
            resumer_identity=request.resumer_identity,
            resumer_model_identity=request.resumer_model_identity,
            next_action=next_action,
            reconcile_step_id=reconcile_step_id,
        )

    def _assert_request_identity(
        self, request: ResumeRequest, checkpoint: ResumeCheckpoint
    ) -> None:
        expected = {
            "workbench_session_id": checkpoint.workbench_session_id,
            "task_id": checkpoint.task_id,
            "operation_id": checkpoint.operation_id,
            "attempt_id": checkpoint.attempt_id,
            "target_identity": checkpoint.target_identity,
        }
        for field_name, value in expected.items():
            if getattr(request, field_name) != value:
                raise ResumeBlocked(
                    "IDENTITY_MISMATCH",
                    f"resume request {field_name} does not match checkpoint",
                )

    def _load_bound_notebook(self, checkpoint: ResumeCheckpoint) -> WorkbenchNotebook:
        try:
            notebook = self.store.load_notebook(checkpoint.workbench_session_id)
        except FileNotFoundError as exc:
            raise ResumeBlocked("NOTEBOOK_MISSING", "durable notebook is missing") from exc
        for field_name in (
            "workbench_session_id",
            "task_id",
            "operation_id",
            "attempt_id",
            "target_identity",
        ):
            if getattr(notebook, field_name) != getattr(checkpoint, field_name):
                raise ResumeBlocked(
                    "NOTEBOOK_IDENTITY_MISMATCH",
                    f"notebook {field_name} does not match checkpoint",
                )
        if notebook.revision != checkpoint.notebook_revision:
            raise ResumeBlocked(
                "STALE_NOTEBOOK_REVISION",
                "checkpoint notebook revision is not the durable notebook revision",
            )
        if notebook.content_hash != checkpoint.notebook_hash:
            raise ResumeBlocked(
                "STALE_NOTEBOOK_HASH",
                "checkpoint notebook hash is not the durable notebook hash",
            )
        return notebook

    def _validate_references(
        self, checkpoint: ResumeCheckpoint, notebook: WorkbenchNotebook
    ) -> None:
        if tuple(notebook.observation_refs) != tuple(checkpoint.observation_refs):
            raise ResumeBlocked(
                "OBSERVATION_LINEAGE_MISMATCH",
                "checkpoint observation refs do not match notebook snapshot",
            )
        if tuple(notebook.artifact_refs) != tuple(checkpoint.artifact_refs):
            raise ResumeBlocked(
                "ARTIFACT_LINEAGE_MISMATCH",
                "checkpoint artifact refs do not match notebook snapshot",
            )
        if set(checkpoint.completed_step_ids) != set(checkpoint.observation_refs):
            raise ResumeBlocked(
                "COMPLETED_STEP_LINEAGE_MISMATCH",
                "completed steps must exactly match durable observation refs",
            )

        for step_id in checkpoint.completed_step_ids:
            try:
                action = self.store.load_action(
                    checkpoint.workbench_session_id, step_id
                )
                observation = self.store.load_observation(
                    checkpoint.workbench_session_id, step_id
                )
            except FileNotFoundError as exc:
                raise ResumeBlocked(
                    "DURABLE_REFERENCE_MISSING",
                    f"completed step {step_id} is missing action/observation state",
                ) from exc
            try:
                observation.assert_binds(action)
            except ValueError as exc:
                raise ResumeBlocked(
                    "ACTION_OBSERVATION_BINDING_MISMATCH",
                    f"completed step {step_id} has mismatched action/observation evidence",
                ) from exc
            if observation.outcome is ObservationOutcome.OUTCOME_UNKNOWN:
                raise ResumeBlocked(
                    "OUTCOME_UNKNOWN_IN_COMPLETED_LINEAGE",
                    f"completed step {step_id} cannot have OUTCOME_UNKNOWN",
                )

        for artifact_id in checkpoint.artifact_refs:
            try:
                artifact = self.store.load_artifact(
                    checkpoint.workbench_session_id, artifact_id
                )
            except FileNotFoundError as exc:
                raise ResumeBlocked(
                    "DURABLE_REFERENCE_MISSING",
                    f"artifact {artifact_id} is missing",
                ) from exc
            if artifact.workbench_session_id != checkpoint.workbench_session_id:
                raise ResumeBlocked(
                    "ARTIFACT_IDENTITY_MISMATCH",
                    f"artifact {artifact_id} is bound to another session",
                )

    def _validate_lineage(self, checkpoint: ResumeCheckpoint) -> None:
        chain: list[ResumeCheckpoint] = [checkpoint]
        cursor = checkpoint
        seen_ids = {checkpoint.checkpoint_id}
        while cursor.sequence > 1:
            try:
                previous = self.store.load_resume_checkpoint(
                    cursor.workbench_session_id, cursor.previous_checkpoint_id
                )
            except FileNotFoundError as exc:
                raise ResumeBlocked(
                    "LINEAGE_PREDECESSOR_MISSING",
                    f"checkpoint {cursor.checkpoint_id} predecessor is missing",
                ) from exc
            if previous.checkpoint_id in seen_ids:
                raise ResumeBlocked("LINEAGE_CYCLE", "resume checkpoint lineage contains a cycle")
            if previous.checkpoint_hash != cursor.previous_checkpoint_hash:
                raise ResumeBlocked(
                    "LINEAGE_HASH_MISMATCH",
                    f"checkpoint {cursor.checkpoint_id} predecessor hash mismatch",
                )
            if previous.sequence != cursor.sequence - 1:
                raise ResumeBlocked(
                    "LINEAGE_SEQUENCE_GAP",
                    "resume checkpoint sequence is not contiguous",
                )
            self._assert_same_identity(previous, cursor)
            self._validate_transition(previous, cursor)
            seen_ids.add(previous.checkpoint_id)
            chain.append(previous)
            cursor = previous

        if cursor.sequence != 1 or cursor.previous_checkpoint_id:
            raise ResumeBlocked(
                "LINEAGE_ROOT_INVALID", "resume checkpoint lineage root is invalid"
            )

    def _assert_same_identity(
        self, previous: ResumeCheckpoint, current: ResumeCheckpoint
    ) -> None:
        for field_name in (
            "workbench_session_id",
            "task_id",
            "operation_id",
            "attempt_id",
            "target_identity",
        ):
            if getattr(previous, field_name) != getattr(current, field_name):
                raise ResumeBlocked(
                    "LINEAGE_IDENTITY_MISMATCH",
                    f"checkpoint lineage changed {field_name}",
                )

    def _validate_transition(
        self, previous: ResumeCheckpoint, current: ResumeCheckpoint
    ) -> None:
        self._assert_same_identity(previous, current)
        if current.sequence != previous.sequence + 1:
            raise ResumeBlocked(
                "LINEAGE_SEQUENCE_GAP", "checkpoint sequence must advance by exactly one"
            )
        if current.previous_checkpoint_id != previous.checkpoint_id:
            raise ResumeBlocked(
                "LINEAGE_ID_MISMATCH", "checkpoint predecessor ID is not current head"
            )
        if current.previous_checkpoint_hash != previous.checkpoint_hash:
            raise ResumeBlocked(
                "LINEAGE_HASH_MISMATCH", "checkpoint predecessor hash is not current head"
            )

        previous_completed = previous.completed_step_ids
        current_completed = current.completed_step_ids
        if tuple(current_completed[: len(previous_completed)]) != tuple(previous_completed):
            raise ResumeBlocked(
                "COMPLETED_STEP_FORK",
                "completed-step lineage must be append-only and ordered",
            )

        if previous.phase is ResumePhase.READY_FOR_ACTION:
            expected = previous.next_action
            assert expected is not None
            if current.phase is not ResumePhase.IN_FLIGHT:
                raise ResumeBlocked(
                    "INVALID_PHASE_TRANSITION",
                    "READY_FOR_ACTION may only advance to IN_FLIGHT",
                )
            assert current.active_action is not None
            if current.active_action.content_hash != expected.content_hash:
                raise ResumeBlocked(
                    "ACTION_SUBSTITUTION",
                    "IN_FLIGHT action does not match durable next action",
                )
            if current_completed != previous_completed:
                raise ResumeBlocked(
                    "PREMATURE_COMPLETION",
                    "starting an action must not alter completed-step lineage",
                )
            if (
                current.notebook_revision != previous.notebook_revision
                or current.notebook_hash != previous.notebook_hash
                or current.observation_refs != previous.observation_refs
                or current.artifact_refs != previous.artifact_refs
            ):
                raise ResumeBlocked(
                    "PREMATURE_STATE_CHANGE",
                    "starting an action must preserve the bound notebook snapshot",
                )
            return

        if previous.phase is ResumePhase.IN_FLIGHT:
            active = previous.active_action
            assert active is not None
            try:
                durable_action = self.store.load_action(
                    previous.workbench_session_id, active.step_id
                )
            except FileNotFoundError as exc:
                raise ResumeBlocked(
                    "INFLIGHT_ACTION_MISSING",
                    "in-flight action is missing durable ActionCell state",
                ) from exc
            if durable_action.content_hash != active.content_hash:
                raise ResumeBlocked(
                    "ACTION_SUBSTITUTION",
                    "durable in-flight action differs from checkpoint",
                )

            observation = None
            if self.store.observation_exists(
                previous.workbench_session_id, active.step_id
            ):
                observation = self.store.load_observation(
                    previous.workbench_session_id, active.step_id
                )
                try:
                    observation.assert_binds(active)
                except ValueError as exc:
                    raise ResumeBlocked(
                        "ACTION_OBSERVATION_BINDING_MISMATCH",
                        "in-flight observation does not bind active action",
                    ) from exc

            if observation is None or observation.outcome is ObservationOutcome.OUTCOME_UNKNOWN:
                if current.phase is not ResumePhase.RECONCILE_REQUIRED:
                    raise ResumeBlocked(
                        "RECONCILIATION_REQUIRED",
                        "unresolved in-flight action cannot advance to new work",
                    )
                if current.completed_step_ids != previous.completed_step_ids:
                    raise ResumeBlocked(
                        "PREMATURE_COMPLETION",
                        "unresolved in-flight action cannot be marked completed",
                    )
                return

            if current.phase not in (
                ResumePhase.READY_FOR_ACTION,
                ResumePhase.COMPLETE,
            ):
                raise ResumeBlocked(
                    "INVALID_PHASE_TRANSITION",
                    "terminal observation must advance to READY_FOR_ACTION or COMPLETE",
                )
            expected_completed = previous.completed_step_ids + (active.step_id,)
            if current.completed_step_ids != expected_completed:
                raise ResumeBlocked(
                    "COMPLETION_LINEAGE_MISMATCH",
                    "terminal action must append exactly one completed step",
                )
            if active.step_id not in current.observation_refs:
                raise ResumeBlocked(
                    "OBSERVATION_LINEAGE_MISMATCH",
                    "terminal action observation is missing from next checkpoint",
                )
            if current.notebook_revision < previous.notebook_revision:
                raise ResumeBlocked(
                    "STALE_NOTEBOOK_REVISION",
                    "checkpoint transition cannot move notebook revision backwards",
                )
            return

        if previous.phase is ResumePhase.RECONCILE_REQUIRED:
            raise ResumeBlocked(
                "RECONCILIATION_REQUIRED",
                "G4 does not self-authorize continuation after reconciliation is required",
            )

        raise ResumeBlocked(
            "SESSION_ALREADY_COMPLETE", "COMPLETE checkpoint has no successor"
        )

    def _assert_active_action(
        self, checkpoint: ResumeCheckpoint, action: ActionCell
    ) -> None:
        try:
            durable_action = self.store.load_action(
                checkpoint.workbench_session_id, action.step_id
            )
        except FileNotFoundError as exc:
            raise ResumeBlocked(
                "INFLIGHT_ACTION_MISSING",
                "active action is missing durable ActionCell state",
            ) from exc
        if durable_action.content_hash != action.content_hash:
            raise ResumeBlocked(
                "ACTION_SUBSTITUTION",
                "durable active action differs from checkpoint",
            )
        if self.store.observation_exists(
            checkpoint.workbench_session_id, action.step_id
        ):
            observation = self.store.load_observation(
                checkpoint.workbench_session_id, action.step_id
            )
            try:
                observation.assert_binds(action)
            except ValueError as exc:
                raise ResumeBlocked(
                    "ACTION_OBSERVATION_BINDING_MISMATCH",
                    "active observation does not bind active action",
                ) from exc
            if (
                checkpoint.phase is ResumePhase.RECONCILE_REQUIRED
                and observation.outcome is not ObservationOutcome.OUTCOME_UNKNOWN
            ):
                raise ResumeBlocked(
                    "CHECKPOINT_STALE_AFTER_TERMINAL_OBSERVATION",
                    "reconciliation checkpoint is stale relative to terminal observation",
                )
