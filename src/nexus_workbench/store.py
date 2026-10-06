from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, TypeVar

from .model import ActionCell, Candidate, ObservationBundle, WorkbenchNotebook
from .resume import ArtifactRecord, ResumeCheckpoint, ResumeHead, ResumePhase

T = TypeVar("T")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


def _safe_id(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError(f"{field_name} contains unsafe path material")
    return value


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


class JsonWorkbenchStore:
    """Atomic durable JSON store for explicit Workbench state.

    Chat transcript, provider session, model identity, and interpreter memory
    are intentionally absent from durable task truth.
    """

    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    def _path(self, kind: str, *parts: str) -> Path:
        safe = [_safe_id(part, "state identity") for part in parts]
        if not safe:
            raise ValueError("state path requires at least one identity")
        directory = (self.root / kind).resolve()
        path = directory
        for part in safe[:-1]:
            path = path / part
        path = (path / f"{safe[-1]}.json").resolve()
        if not path.is_relative_to(directory):
            raise ValueError("state path escapes Workbench store")
        return path

    def _lock_path(self, workbench_session_id: str) -> Path:
        session = _safe_id(workbench_session_id, "workbench_session_id")
        directory = (self.root / "locks").resolve()
        path = (directory / f"{session}.lock").resolve()
        if not path.is_relative_to(directory):
            raise ValueError("lock path escapes Workbench store")
        return path

    @contextmanager
    def _session_lock(self, workbench_session_id: str) -> Iterator[None]:
        path = self._lock_path(workbench_session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _write(self, path: Path, payload: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=True) + "\n"
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=".wb-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
            temp_path = Path(handle.name)
        temp_path.replace(path)
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    @staticmethod
    def _read(path: Path, loader: Callable[[Mapping[str, Any]], T]) -> T:
        data = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
        if not isinstance(data, Mapping):
            raise ValueError("Workbench state must be a JSON object")
        return loader(data)

    def _save_immutable(
        self,
        path: Path,
        payload: Mapping[str, Any],
        loader: Callable[[Mapping[str, Any]], T],
        value: T,
        label: str,
    ) -> Path:
        if path.exists():
            existing = self._read(path, loader)
            if existing != value:
                raise ValueError(
                    f"{label} is immutable and already exists with different content"
                )
            return path
        self._write(path, payload)
        return path

    def save_notebook(self, notebook: WorkbenchNotebook) -> Path:
        path = self._path("notebooks", notebook.workbench_session_id)
        with self._session_lock(notebook.workbench_session_id):
            if path.exists():
                current = self._read(path, WorkbenchNotebook.from_dict)
                if current == notebook:
                    return path
                if notebook.revision != current.revision + 1:
                    raise ValueError(
                        "notebook revision must advance durable state by exactly one"
                    )
            elif notebook.revision != 1:
                raise ValueError("first durable notebook revision must be 1")
            self._write(path, notebook.to_dict())
            return path

    def load_notebook(self, workbench_session_id: str) -> WorkbenchNotebook:
        return self._read(
            self._path("notebooks", workbench_session_id),
            WorkbenchNotebook.from_dict,
        )

    def save_action(self, action: ActionCell) -> Path:
        path = self._path("actions", action.workbench_session_id, action.step_id)
        with self._session_lock(action.workbench_session_id):
            return self._save_immutable(
                path, action.to_dict(), ActionCell.from_dict, action, "ActionCell"
            )

    def load_action(self, workbench_session_id: str, step_id: str) -> ActionCell:
        return self._read(
            self._path("actions", workbench_session_id, step_id),
            ActionCell.from_dict,
        )

    def action_exists(self, workbench_session_id: str, step_id: str) -> bool:
        return self._path("actions", workbench_session_id, step_id).exists()

    def save_observation(self, observation: ObservationBundle) -> Path:
        path = self._path(
            "observations", observation.workbench_session_id, observation.step_id
        )
        with self._session_lock(observation.workbench_session_id):
            return self._save_immutable(
                path,
                observation.to_dict(),
                ObservationBundle.from_dict,
                observation,
                "ObservationBundle",
            )

    def load_observation(
        self, workbench_session_id: str, step_id: str
    ) -> ObservationBundle:
        return self._read(
            self._path("observations", workbench_session_id, step_id),
            ObservationBundle.from_dict,
        )

    def observation_exists(self, workbench_session_id: str, step_id: str) -> bool:
        return self._path("observations", workbench_session_id, step_id).exists()

    def save_artifact(self, artifact: ArtifactRecord) -> Path:
        path = self._path(
            "artifacts", artifact.workbench_session_id, artifact.artifact_id
        )
        with self._session_lock(artifact.workbench_session_id):
            return self._save_immutable(
                path,
                artifact.to_dict(),
                ArtifactRecord.from_dict,
                artifact,
                "ArtifactRecord",
            )

    def load_artifact(
        self, workbench_session_id: str, artifact_id: str
    ) -> ArtifactRecord:
        return self._read(
            self._path("artifacts", workbench_session_id, artifact_id),
            ArtifactRecord.from_dict,
        )

    def artifact_exists(self, workbench_session_id: str, artifact_id: str) -> bool:
        return self._path("artifacts", workbench_session_id, artifact_id).exists()

    def save_candidate(self, candidate: Candidate) -> Path:
        path = self._path("candidates", candidate.workbench_session_id)
        self._write(path, candidate.to_dict())
        return path

    def load_candidate(self, workbench_session_id: str) -> Candidate:
        return self._read(
            self._path("candidates", workbench_session_id), Candidate.from_dict
        )

    def save_resume_checkpoint(self, checkpoint: ResumeCheckpoint) -> Path:
        path = self._path(
            "resume-checkpoints",
            checkpoint.workbench_session_id,
            checkpoint.checkpoint_id,
        )
        head_path = self._path("resume-heads", checkpoint.workbench_session_id)

        with self._session_lock(checkpoint.workbench_session_id):
            current_head: ResumeHead | None = None
            if head_path.exists():
                current_head = self._read(head_path, ResumeHead.from_dict)

            if (
                current_head is not None
                and current_head.checkpoint_id == checkpoint.checkpoint_id
            ):
                if (
                    current_head.checkpoint_hash != checkpoint.checkpoint_hash
                    or current_head.sequence != checkpoint.sequence
                ):
                    raise ValueError(
                        "resume checkpoint ID already names a different durable head"
                    )
                existing = self._read(path, ResumeCheckpoint.from_dict)
                if existing != checkpoint:
                    raise ValueError(
                        "resume checkpoint is immutable and differs from durable content"
                    )
                return path

            if current_head is None:
                if (
                    checkpoint.sequence != 1
                    or checkpoint.previous_checkpoint_id
                    or checkpoint.previous_checkpoint_hash
                ):
                    raise ValueError(
                        "first resume checkpoint must start sequence 1 without predecessor"
                    )
            else:
                if checkpoint.sequence != current_head.sequence + 1:
                    raise ValueError(
                        "resume checkpoint sequence does not follow durable head"
                    )
                if checkpoint.previous_checkpoint_id != current_head.checkpoint_id:
                    raise ValueError("resume checkpoint predecessor ID is stale")
                if checkpoint.previous_checkpoint_hash != current_head.checkpoint_hash:
                    raise ValueError("resume checkpoint predecessor hash is stale")

            if path.exists():
                existing = self._read(path, ResumeCheckpoint.from_dict)
                if existing != checkpoint:
                    raise ValueError(
                        "resume checkpoint is immutable and already exists with different content"
                    )
            else:
                self._write(path, checkpoint.to_dict())

            head = ResumeHead(
                workbench_session_id=checkpoint.workbench_session_id,
                checkpoint_id=checkpoint.checkpoint_id,
                checkpoint_hash=checkpoint.checkpoint_hash,
                sequence=checkpoint.sequence,
            )
            self._write(head_path, head.to_dict())
            return path

    def claim_ready_action(
        self,
        ready_checkpoint: ResumeCheckpoint,
        inflight_checkpoint: ResumeCheckpoint,
    ) -> Path:
        """Atomically fence one READY action claim and advance the durable head.

        Exactly one competing resumer can advance the bound READY checkpoint.
        A crash after materializing the ActionCell but before head advancement
        leaves durable evidence that blocks blind replay on the next resume.
        """
        session_id = ready_checkpoint.workbench_session_id
        if inflight_checkpoint.workbench_session_id != session_id:
            raise ValueError("resume claim session mismatch")
        if inflight_checkpoint.phase is not ResumePhase.IN_FLIGHT:
            raise ValueError("resume claim successor must be IN_FLIGHT")
        action = inflight_checkpoint.active_action
        if action is None:
            raise ValueError("resume claim successor requires active_action")

        head_path = self._path("resume-heads", session_id)
        checkpoint_path = self._path(
            "resume-checkpoints",
            session_id,
            inflight_checkpoint.checkpoint_id,
        )
        action_path = self._path("actions", session_id, action.step_id)

        with self._session_lock(session_id):
            head = self._read(head_path, ResumeHead.from_dict)
            if (
                head.checkpoint_id != ready_checkpoint.checkpoint_id
                or head.checkpoint_hash != ready_checkpoint.checkpoint_hash
                or head.sequence != ready_checkpoint.sequence
            ):
                raise ValueError("resume READY claim lost durable-head race")
            if inflight_checkpoint.sequence != ready_checkpoint.sequence + 1:
                raise ValueError("resume claim sequence is not current head + 1")
            if (
                inflight_checkpoint.previous_checkpoint_id
                != ready_checkpoint.checkpoint_id
                or inflight_checkpoint.previous_checkpoint_hash
                != ready_checkpoint.checkpoint_hash
            ):
                raise ValueError("resume claim predecessor does not bind current head")
            if action.workbench_session_id != session_id:
                raise ValueError("resume claim action session mismatch")
            if action.step_id in ready_checkpoint.completed_step_ids:
                raise ValueError("resume claim action is already completed")
            if action_path.exists() or self.observation_exists(session_id, action.step_id):
                raise ValueError(
                    "resume READY action already materialized; reconcile instead"
                )
            if checkpoint_path.exists():
                raise ValueError("resume claim checkpoint already exists")

            self._write(action_path, action.to_dict())
            self._write(checkpoint_path, inflight_checkpoint.to_dict())
            new_head = ResumeHead(
                workbench_session_id=session_id,
                checkpoint_id=inflight_checkpoint.checkpoint_id,
                checkpoint_hash=inflight_checkpoint.checkpoint_hash,
                sequence=inflight_checkpoint.sequence,
            )
            self._write(head_path, new_head.to_dict())
            return checkpoint_path

    def load_resume_checkpoint(
        self, workbench_session_id: str, checkpoint_id: str
    ) -> ResumeCheckpoint:
        return self._read(
            self._path(
                "resume-checkpoints", workbench_session_id, checkpoint_id
            ),
            ResumeCheckpoint.from_dict,
        )

    def load_resume_head(self, workbench_session_id: str) -> ResumeHead:
        return self._read(
            self._path("resume-heads", workbench_session_id),
            ResumeHead.from_dict,
        )
