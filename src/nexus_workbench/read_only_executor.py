from __future__ import annotations

import ast
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from typing import Any, Mapping

from .model import ActionCell, ActionMode, ObservationBundle, ObservationOutcome

READ_ONLY_EXECUTOR_CLAIM_CEILING = "G2_READ_ONLY_EXECUTOR_CANARY_ONLY"
_ALLOWED_REPO_OPS = frozenset(
    {"repo.read", "repo.search", "git.status", "git.diff", "test.discover"}
)
_ALLOWED_SCRATCH_OPS = frozenset({"artifact.write"})
_MAX_READ_BYTES = 1_000_000
_MAX_SEARCH_RESULTS = 200
_MAX_ARTIFACT_BYTES = 1_000_000


class ReadOnlyPolicyError(ValueError):
    """A fail-closed G2 policy denial."""


@dataclass(frozen=True)
class TargetSnapshot:
    head: str
    tree: str
    git_status: str
    manifest_hash: str
    manifest_entries: Mapping[str, str]

    @property
    def identity(self) -> str:
        body = {
            "head": self.head,
            "tree": self.tree,
            "git_status": self.git_status,
            "manifest_hash": self.manifest_hash,
        }
        raw = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return "workbench-target:v1:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def summary(self) -> dict[str, Any]:
        return {
            "identity": self.identity,
            "head": self.head,
            "tree": self.tree,
            "git_status": self.git_status,
            "manifest_hash": self.manifest_hash,
            "manifest_entry_count": len(self.manifest_entries),
        }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _manifest(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}

    def visit(directory: Path) -> None:
        entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
        for entry in entries:
            path = Path(entry.path)
            if directory == root and entry.name == ".git":
                continue
            rel = path.relative_to(root).as_posix()
            info = entry.stat(follow_symlinks=False)
            mode = stat.S_IMODE(info.st_mode)
            if entry.is_symlink():
                result[rel] = f"L:{mode:o}:{os.readlink(path)}"
                continue
            if entry.is_dir(follow_symlinks=False):
                result[rel] = f"D:{mode:o}"
                visit(path)
                continue
            if entry.is_file(follow_symlinks=False):
                digest = hashlib.sha256()
                with path.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
                result[rel] = f"F:{mode:o}:{info.st_size}:{digest.hexdigest()}"
                continue
            result[rel] = f"S:{mode:o}:{stat.S_IFMT(info.st_mode):o}"

    visit(root)
    return result


def _manifest_hash(entries: Mapping[str, str]) -> str:
    raw = json.dumps(dict(sorted(entries.items())), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _changed_paths(before: TargetSnapshot, after: TargetSnapshot) -> tuple[str, ...]:
    paths = sorted(
        key
        for key in set(before.manifest_entries) | set(after.manifest_entries)
        if before.manifest_entries.get(key) != after.manifest_entries.get(key)
    )
    if (
        before.head != after.head
        or before.tree != after.tree
        or before.git_status != after.git_status
    ) and not paths:
        paths.append("@git-state")
    return tuple(paths)


class LocalReadOnlyExecutor:
    """Standalone G2 executor for one exact clean Git target.

    The executor exposes only fixed read-only repository probes plus artifact
    creation inside a disjoint Workbench-owned scratch directory. It does not
    expose arbitrary shell, Python execution, source writes, or transport
    authority.
    """

    def __init__(self, target_root: Path, scratch_root: Path, baseline: TargetSnapshot) -> None:
        self.target_root = target_root
        self.scratch_root = scratch_root
        self.baseline = baseline
        digest = hashlib.sha256(str(target_root).encode("utf-8")).hexdigest()[:16]
        self._identity = f"local-readonly-v1:{digest}"

    @classmethod
    def bind(cls, target_root: str | os.PathLike[str], scratch_root: str | os.PathLike[str]) -> "LocalReadOnlyExecutor":
        target = Path(target_root).expanduser().resolve(strict=True)
        if not target.is_dir():
            raise ReadOnlyPolicyError("target_root must be a directory")

        scratch = Path(scratch_root).expanduser().resolve(strict=False)
        if scratch == target or scratch.is_relative_to(target) or target.is_relative_to(scratch):
            raise ReadOnlyPolicyError("scratch_root must be physically disjoint from target_root")
        scratch.mkdir(parents=True, exist_ok=True)
        scratch = scratch.resolve(strict=True)
        if scratch == target or scratch.is_relative_to(target) or target.is_relative_to(scratch):
            raise ReadOnlyPolicyError("scratch_root must be physically disjoint from target_root")

        executor = cls.__new__(cls)
        executor.target_root = target
        executor.scratch_root = scratch
        digest = hashlib.sha256(str(target).encode("utf-8")).hexdigest()[:16]
        executor._identity = f"local-readonly-v1:{digest}"

        top = executor._git("rev-parse", "--show-toplevel").strip()
        if Path(top).resolve(strict=True) != target:
            raise ReadOnlyPolicyError("target_root must be the exact Git worktree root")

        baseline = executor._snapshot()
        if baseline.git_status:
            raise ReadOnlyPolicyError("G2 target must be clean with no untracked or ignored worktree state")
        executor.baseline = baseline
        return executor

    @property
    def identity(self) -> str:
        return self._identity

    @property
    def target_identity(self) -> str:
        return self.baseline.identity

    def snapshot(self) -> TargetSnapshot:
        return self._snapshot()

    def execute(self, action: ActionCell) -> ObservationBundle:
        started = _utc_now()
        before = self._snapshot()
        outcome = ObservationOutcome.BLOCKED
        result_summary = ""
        result: dict[str, Any] = {}
        artifact_refs: tuple[str, ...] = ()

        if before.identity != self.baseline.identity:
            result_summary = "target identity drifted before action"
        else:
            try:
                payload = self._parse_action(action)
                op = str(payload["op"])
                result, artifact_refs = self._dispatch(action, op, payload)
                outcome = ObservationOutcome.SUCCEEDED
                result_summary = f"{op} succeeded"
            except ReadOnlyPolicyError as exc:
                result_summary = str(exc)
                outcome = ObservationOutcome.BLOCKED
            except Exception as exc:
                result_summary = f"executor failure: {type(exc).__name__}: {exc}"
                outcome = ObservationOutcome.FAILED

        after = self._snapshot()
        changed = _changed_paths(before, after)
        if outcome == ObservationOutcome.SUCCEEDED and (
            after.identity != before.identity or after.identity != self.baseline.identity
        ):
            outcome = ObservationOutcome.FAILED
            result_summary = "physical target mutation or identity drift detected after action"

        physical = {
            "claim_ceiling": READ_ONLY_EXECUTOR_CLAIM_CEILING,
            "target_root": str(self.target_root),
            "scratch_root": str(self.scratch_root),
            "baseline": self.baseline.summary(),
            "before": before.summary(),
            "after": after.summary(),
            "result": result,
        }
        return ObservationBundle(
            workbench_session_id=action.workbench_session_id,
            step_id=action.step_id,
            action_hash=action.content_hash,
            executor_identity=self.identity,
            started_at=started,
            finished_at=_utc_now(),
            outcome=outcome,
            result_summary=result_summary,
            physical_readback=physical,
            artifact_refs=artifact_refs,
            changed_target_paths=changed,
        )

    def _parse_action(self, action: ActionCell) -> dict[str, Any]:
        if action.mode == ActionMode.EFFECTFUL:
            raise ReadOnlyPolicyError("G2 rejects EFFECTFUL Action Cells")
        try:
            payload = json.loads(action.code_or_action)
        except json.JSONDecodeError as exc:
            raise ReadOnlyPolicyError("code_or_action must be one JSON object") from exc
        if not isinstance(payload, dict):
            raise ReadOnlyPolicyError("code_or_action must be one JSON object")
        op = payload.get("op")
        if not isinstance(op, str) or not op:
            raise ReadOnlyPolicyError("action payload requires non-empty op")
        if op not in _ALLOWED_REPO_OPS | _ALLOWED_SCRATCH_OPS:
            raise ReadOnlyPolicyError(f"unsupported G2 operation: {op}")
        if op not in action.requested_capabilities:
            raise ReadOnlyPolicyError("action op is not present in requested_capabilities")
        if op in _ALLOWED_REPO_OPS and action.mode != ActionMode.READ_ONLY_PROBE:
            raise ReadOnlyPolicyError(f"{op} requires READ_ONLY_PROBE mode")
        if op in _ALLOWED_SCRATCH_OPS and action.mode != ActionMode.PURE_COMPUTE:
            raise ReadOnlyPolicyError(f"{op} requires PURE_COMPUTE mode")
        return payload

    def _dispatch(
        self, action: ActionCell, op: str, payload: Mapping[str, Any]
    ) -> tuple[dict[str, Any], tuple[str, ...]]:
        del action
        if op == "repo.read":
            return self._repo_read(payload), ()
        if op == "repo.search":
            return self._repo_search(payload), ()
        if op == "git.status":
            self._require_keys(payload, {"op"})
            return {"status": self._git("status", "--porcelain=v2", "--branch", "--untracked-files=all")}, ()
        if op == "git.diff":
            return self._git_diff(payload), ()
        if op == "test.discover":
            return self._test_discover(payload), ()
        if op == "artifact.write":
            result, ref = self._artifact_write(payload)
            return result, (ref,)
        raise ReadOnlyPolicyError(f"unsupported G2 operation: {op}")

    @staticmethod
    def _require_keys(payload: Mapping[str, Any], allowed: set[str]) -> None:
        extra = sorted(set(payload) - allowed)
        if extra:
            raise ReadOnlyPolicyError(f"unsupported action fields: {extra}")

    def _repo_read(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._require_keys(payload, {"op", "path"})
        path = self._target_path(payload.get("path"))
        if not path.is_file():
            raise ReadOnlyPolicyError("repo.read path must be a regular file")
        data = path.read_bytes()
        if len(data) > _MAX_READ_BYTES:
            raise ReadOnlyPolicyError("repo.read file exceeds G2 size limit")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ReadOnlyPolicyError("repo.read supports UTF-8 text files only") from exc
        return {
            "path": path.relative_to(self.target_root).as_posix(),
            "bytes": len(data),
            "sha256": _sha256_bytes(data),
            "text": text,
        }

    def _repo_search(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._require_keys(payload, {"op", "query", "path", "max_results"})
        query = payload.get("query")
        if not isinstance(query, str) or not query:
            raise ReadOnlyPolicyError("repo.search requires non-empty query")
        base = self._target_path(payload.get("path", "."))
        max_results = payload.get("max_results", 50)
        if isinstance(max_results, bool) or not isinstance(max_results, int) or not 1 <= max_results <= _MAX_SEARCH_RESULTS:
            raise ReadOnlyPolicyError(f"max_results must be 1..{_MAX_SEARCH_RESULTS}")

        files: list[Path] = []
        if base.is_file():
            files = [base]
        elif base.is_dir():
            for directory, dirs, names in os.walk(base, followlinks=False):
                directory_path = Path(directory)
                dirs[:] = sorted(
                    name
                    for name in dirs
                    if not (directory_path == self.target_root and name == ".git")
                    and not (directory_path / name).is_symlink()
                )
                for name in sorted(names):
                    path = directory_path / name
                    if path.is_symlink():
                        continue
                    files.append(path)
        else:
            raise ReadOnlyPolicyError("repo.search path must be a file or directory")

        matches: list[dict[str, Any]] = []
        for path in files:
            resolved = path.resolve(strict=True)
            if resolved != self.target_root and not resolved.is_relative_to(self.target_root):
                raise ReadOnlyPolicyError("repo.search encountered target path escape")
            if not resolved.is_file() or resolved.stat().st_size > _MAX_READ_BYTES:
                continue
            try:
                lines = resolved.read_text(encoding="utf-8").splitlines()
            except (UnicodeDecodeError, OSError):
                continue
            for line_no, line in enumerate(lines, start=1):
                if query in line:
                    matches.append(
                        {
                            "path": resolved.relative_to(self.target_root).as_posix(),
                            "line": line_no,
                            "text": line,
                        }
                    )
                    if len(matches) >= max_results:
                        return {"query": query, "matches": matches, "truncated": True}
        return {"query": query, "matches": matches, "truncated": False}

    def _git_diff(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._require_keys(payload, {"op", "path"})
        suffix: tuple[str, ...] = ()
        if "path" in payload:
            path = self._target_path(payload["path"])
            rel = path.relative_to(self.target_root).as_posix()
            suffix = ("--", rel)
        unstaged = self._git("diff", "--no-ext-diff", "--no-textconv", *suffix)
        staged = self._git("diff", "--cached", "--no-ext-diff", "--no-textconv", *suffix)
        return {"unstaged": unstaged, "staged": staged}

    def _test_discover(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._require_keys(payload, {"op", "path"})
        base = self._target_path(payload.get("path", "tests"))
        if not base.exists():
            return {"files": [], "tests": [], "parse_errors": []}
        candidates: list[Path] = []
        if base.is_file():
            candidates = [base]
        else:
            for directory, dirs, names in os.walk(base, followlinks=False):
                directory_path = Path(directory)
                dirs[:] = sorted(name for name in dirs if not (directory_path / name).is_symlink())
                for name in sorted(names):
                    if name.startswith("test_") and name.endswith(".py") or name.endswith("_test.py"):
                        path = directory_path / name
                        if not path.is_symlink():
                            candidates.append(path)

        files: list[str] = []
        tests: list[str] = []
        parse_errors: list[dict[str, str]] = []
        for path in candidates:
            resolved = path.resolve(strict=True)
            if resolved != self.target_root and not resolved.is_relative_to(self.target_root):
                raise ReadOnlyPolicyError("test.discover encountered target path escape")
            data = resolved.read_bytes()
            if len(data) > _MAX_READ_BYTES:
                parse_errors.append({"path": resolved.relative_to(self.target_root).as_posix(), "error": "size limit"})
                continue
            rel = resolved.relative_to(self.target_root).as_posix()
            files.append(rel)
            try:
                tree = ast.parse(data.decode("utf-8"), filename=rel)
            except (UnicodeDecodeError, SyntaxError) as exc:
                parse_errors.append({"path": rel, "error": f"{type(exc).__name__}: {exc}"})
                continue
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
                    tests.append(f"{rel}::{node.name}")
                elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                    for child in node.body:
                        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name.startswith("test"):
                            tests.append(f"{rel}::{node.name}::{child.name}")
        return {"files": files, "tests": tests, "parse_errors": parse_errors}

    def _artifact_write(self, payload: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
        self._require_keys(payload, {"op", "path", "content"})
        path_value = payload.get("path")
        content = payload.get("content")
        if not isinstance(content, str):
            raise ReadOnlyPolicyError("artifact.write content must be text")
        data = content.encode("utf-8")
        if len(data) > _MAX_ARTIFACT_BYTES:
            raise ReadOnlyPolicyError("artifact.write exceeds G2 size limit")
        path = self._scratch_path(path_value)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=".artifact-", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        except Exception:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
            raise
        rel = path.relative_to(self.scratch_root).as_posix()
        return {"path": rel, "bytes": len(data), "sha256": _sha256_bytes(data)}, f"scratch:{rel}"

    def _target_path(self, value: Any) -> Path:
        raw = "." if value in (None, "") else value
        if not isinstance(raw, str):
            raise ReadOnlyPolicyError("target path must be a string")
        relative = Path(raw)
        if relative.is_absolute() or ".." in relative.parts:
            raise ReadOnlyPolicyError("target path escape is forbidden")
        try:
            resolved = (self.target_root / relative).resolve(strict=True)
        except FileNotFoundError as exc:
            raise ReadOnlyPolicyError("target path does not exist") from exc
        if resolved != self.target_root and not resolved.is_relative_to(self.target_root):
            raise ReadOnlyPolicyError("target symlink/path escape is forbidden")
        return resolved

    def _scratch_path(self, value: Any) -> Path:
        if not isinstance(value, str) or not value:
            raise ReadOnlyPolicyError("scratch path must be a non-empty string")
        relative = Path(value)
        if relative.is_absolute() or ".." in relative.parts:
            raise ReadOnlyPolicyError("scratch path escape is forbidden")
        resolved = (self.scratch_root / relative).resolve(strict=False)
        if resolved == self.scratch_root or not resolved.is_relative_to(self.scratch_root):
            raise ReadOnlyPolicyError("scratch path escape is forbidden")
        current = resolved.parent
        while current != self.scratch_root:
            if current.exists() and current.is_symlink():
                raise ReadOnlyPolicyError("scratch symlink escape is forbidden")
            current = current.parent
        if resolved.exists() and resolved.is_symlink():
            raise ReadOnlyPolicyError("scratch symlink escape is forbidden")
        return resolved

    def _snapshot(self) -> TargetSnapshot:
        head = self._git("rev-parse", "HEAD").strip()
        tree = self._git("rev-parse", "HEAD^{tree}").strip()
        status_text = self._git(
            "status",
            "--porcelain=v2",
            "--untracked-files=all",
            "--ignored=matching",
        )
        entries = _manifest(self.target_root)
        return TargetSnapshot(
            head=head,
            tree=tree,
            git_status=status_text,
            manifest_hash=_manifest_hash(entries),
            manifest_entries=entries,
        )

    def _git(self, *args: str) -> str:
        env = os.environ.copy()
        env.update(
            {
                "GIT_OPTIONAL_LOCKS": "0",
                "GIT_PAGER": "cat",
                "PAGER": "cat",
            }
        )
        proc = subprocess.run(
            ["git", "-c", "core.fsmonitor=false", *args],
            cwd=self.target_root,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
            check=False,
        )
        if proc.returncode != 0:
            raise ReadOnlyPolicyError(
                f"git {' '.join(args)} failed with exit {proc.returncode}: {proc.stderr.strip()}"
            )
        return proc.stdout
