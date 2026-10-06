from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping, TypeVar

from .model import ActionCell, Candidate, ObservationBundle, WorkbenchNotebook

T = TypeVar("T")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


def _safe_id(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError(f"{field_name} contains unsafe path material")
    return value


class JsonWorkbenchStore:
    """Atomic durable JSON store for explicit Workbench state.

    The store intentionally has no transcript/provider-session field. Physical
    executor process state may disappear without erasing durable Workbench state.
    """

    def __init__(self, root: Path):
        self.root = Path(root)

    def _path(self, kind: str, *parts: str) -> Path:
        safe = [_safe_id(part, "state identity") for part in parts]
        directory = (self.root / kind).resolve()
        path = (directory / ("--".join(safe) + ".json")).resolve()
        if not path.is_relative_to(directory):
            raise ValueError("state path escapes Workbench store")
        return path

    def _write(self, path: Path, payload: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=True) + "\n"
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=".wb-", suffix=".tmp", delete=False) as handle:
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
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, Mapping):
            raise ValueError("Workbench state must be a JSON object")
        return loader(data)

    def save_notebook(self, notebook: WorkbenchNotebook) -> Path:
        path = self._path("notebooks", notebook.workbench_session_id)
        self._write(path, notebook.to_dict())
        return path

    def load_notebook(self, workbench_session_id: str) -> WorkbenchNotebook:
        return self._read(self._path("notebooks", workbench_session_id), WorkbenchNotebook.from_dict)

    def save_action(self, action: ActionCell) -> Path:
        path = self._path("actions", action.workbench_session_id, action.step_id)
        self._write(path, action.to_dict())
        return path

    def load_action(self, workbench_session_id: str, step_id: str) -> ActionCell:
        return self._read(self._path("actions", workbench_session_id, step_id), ActionCell.from_dict)

    def save_observation(self, observation: ObservationBundle) -> Path:
        path = self._path("observations", observation.workbench_session_id, observation.step_id)
        self._write(path, observation.to_dict())
        return path

    def load_observation(self, workbench_session_id: str, step_id: str) -> ObservationBundle:
        return self._read(self._path("observations", workbench_session_id, step_id), ObservationBundle.from_dict)

    def save_candidate(self, candidate: Candidate) -> Path:
        path = self._path("candidates", candidate.workbench_session_id)
        self._write(path, candidate.to_dict())
        return path

    def load_candidate(self, workbench_session_id: str) -> Candidate:
        return self._read(self._path("candidates", workbench_session_id), Candidate.from_dict)
