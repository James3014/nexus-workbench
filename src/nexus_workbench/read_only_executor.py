from __future__ import annotations

import ast
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import pwd
import stat
import subprocess
import sys
import time
from typing import Any, Mapping

from .model import ActionCell, ActionMode, ObservationBundle, ObservationOutcome
from .safe_paths import open_directory, open_parent, open_path

READ_ONLY_EXECUTOR_CLAIM_CEILING = "G2_READ_ONLY_EXECUTOR_CANARY_ONLY"
_ALLOWED_REPO_OPS = frozenset(
    {"repo.read", "repo.search", "git.status", "git.diff", "test.discover"}
)
_ALLOWED_SCRATCH_OPS = frozenset({"artifact.write"})
_MAX_READ_BYTES = 1_000_000
_MAX_SEARCH_RESULTS = 200
_MAX_ARTIFACT_BYTES = 1_000_000
# Every snapshot walks and hashes the whole target, twice per action. These
# ceilings bound that cost; a larger target is refused, not partially hashed.
_MAX_MANIFEST_ENTRIES = 50_000
_GIT_EXECUTABLE_ENV = "NEXUS_WORKBENCH_GIT_EXECUTABLE"
_GIT_CONFIG_PATH = Path("/etc/nexus-workbench/git.conf")
_MAX_MANIFEST_BYTES = 512 * 1024 * 1024
_ENFORCED_GIT_CONFIG = (
    ("core.fsmonitor", "false"), ("core.hooksPath", "/dev/null"),
    ("core.sshCommand", ""), ("core.pager", "cat"), ("core.editor", ""),
    ("core.askPass", ""), ("diff.external", ""), ("include.path", "/dev/null"),
    ("includeIf.gitdir:/__nexus_workbench_never__/.path", "/dev/null"),
    ("protocol.ext.allow", "never"), ("credential.helper", ""),
)


class ReadOnlyPolicyError(ValueError):
    """A fail-closed G2 policy denial."""


@dataclass(frozen=True)
class TargetSnapshot:
    head: str
    tree: str
    git_status: str
    manifest_hash: str
    manifest_entries: Mapping[str, str]
    git_executable: str = ""
    git_source: str = ""
    git_owner: str = ""
    git_mode: str = ""
    git_sha256: str = ""
    git_version: str = ""
    git_replace_ref_check_passed: bool = False

    @property
    def identity(self) -> str:
        body = {
            "head": self.head,
            "tree": self.tree,
            "git_status": self.git_status,
            "manifest_hash": self.manifest_hash,
            "git_executable": self.git_executable,
            "git_source": self.git_source,
            "git_owner": self.git_owner,
            "git_mode": self.git_mode,
            "git_sha256": self.git_sha256,
            "git_version": self.git_version,
            "git_replace_ref_check_passed": self.git_replace_ref_check_passed,
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
            "git_executable": self.git_executable,
            "git_source": self.git_source,
            "git_owner": self.git_owner,
            "git_mode": self.git_mode,
            "git_sha256": self.git_sha256,
            "git_version": self.git_version,
            "git_replace_ref_check_passed": self.git_replace_ref_check_passed,
        }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git_metadata_roots(target: Path, primary_git_dir: Path, primary_common_dir: Path) -> tuple[Path, ...]:
    """Find the root repository and every nested Git directory/gitfile in target."""
    roots: list[Path] = [primary_git_dir, primary_common_dir]
    for current, directories, files in os.walk(target, followlinks=False):
        current_path = Path(current)
        if ".git" in directories:
            entry = current_path / ".git"
            info = os.lstat(entry)
            if stat.S_ISLNK(info.st_mode):
                raise ReadOnlyPolicyError(f"Git metadata symlink is forbidden: {entry}")
            roots.append(entry)
            directories.remove(".git")
        elif ".git" in files:
            entry = current_path / ".git"
            info = os.lstat(entry)
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                raise ReadOnlyPolicyError(f"Git metadata entry is invalid: {entry}")
            try:
                pointer = entry.read_text(encoding="utf-8").strip()
            except (OSError, UnicodeError) as exc:
                raise ReadOnlyPolicyError("Git worktree pointer is invalid") from exc
            if not pointer.startswith("gitdir: "):
                raise ReadOnlyPolicyError("Git worktree pointer is invalid")
            raw = Path(pointer[8:])
            root = raw if raw.is_absolute() else entry.parent / raw
            try:
                roots.append(root.resolve(strict=True))
            except OSError as exc:
                raise ReadOnlyPolicyError("Git worktree pointer is unavailable") from exc

    checked: list[Path] = []
    for root in roots:
        try:
            resolved = root.resolve(strict=True)
        except OSError as exc:
            raise ReadOnlyPolicyError("Git metadata directory is unavailable") from exc
        if resolved != target and not resolved.is_relative_to(target):
            raise ReadOnlyPolicyError("Git metadata directory is outside the target repository")
        if not resolved.is_dir():
            raise ReadOnlyPolicyError("Git metadata directory is invalid")
        checked.append(resolved)
        common_file = resolved / "commondir"
        if common_file.exists():
            try:
                raw_common = Path(common_file.read_text(encoding="utf-8").strip())
                common = raw_common if raw_common.is_absolute() else resolved / raw_common
                common = common.resolve(strict=True)
            except (OSError, UnicodeError) as exc:
                raise ReadOnlyPolicyError("Git common directory is unavailable") from exc
            if common != target and not common.is_relative_to(target):
                raise ReadOnlyPolicyError("Git common directory is outside the target repository")
            checked.append(common)
    return tuple(dict.fromkeys(checked))


def _git_metadata_fingerprint(target: Path, git_dir: Path, common_dir: Path) -> tuple[tuple[str, int, int, int], ...]:
    """Fail closed on redirected or linkable Git metadata and return its inode view."""
    for label, directory in (("git directory", git_dir), ("common directory", common_dir)):
        try:
            resolved = directory.resolve(strict=True)
        except OSError as exc:
            raise ReadOnlyPolicyError(f"Git {label} is unavailable") from exc
        if resolved != target and not resolved.is_relative_to(target):
            raise ReadOnlyPolicyError(f"Git {label} is outside the target repository")

    found: dict[str, os.stat_result] = {}
    roots = _git_metadata_roots(target, git_dir, common_dir)
    for root in roots:
        try:
            root_info = os.lstat(root)
        except OSError as exc:
            raise ReadOnlyPolicyError("Git metadata directory is unavailable") from exc
        if stat.S_ISLNK(root_info.st_mode):
            raise ReadOnlyPolicyError(f"Git metadata symlink is forbidden: {root}")
        if not stat.S_ISDIR(root_info.st_mode):
            raise ReadOnlyPolicyError(f"Git metadata directory is invalid: {root}")
        for current, directories, files in os.walk(root, followlinks=False):
            for name in ("", *directories, *files):
                path = Path(current) if not name else Path(current) / name
                info = os.lstat(path)
                if stat.S_ISLNK(info.st_mode):
                    raise ReadOnlyPolicyError(f"Git metadata symlink is forbidden: {path}")
                real = path.resolve(strict=True)
                if real != target and not real.is_relative_to(target):
                    raise ReadOnlyPolicyError(f"Git metadata path is outside the target repository: {path}")
                if path.name in {"config", "config.worktree"} and stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
                    raise ReadOnlyPolicyError(f"Git config hardlink is forbidden: {path}")
                if path.name in {"gitdir", "commondir"} and stat.S_ISREG(info.st_mode):
                    raw_pointer = Path(path.read_text(encoding="utf-8").strip())
                    pointer = raw_pointer if raw_pointer.is_absolute() else path.parent / raw_pointer
                    real_pointer = pointer.resolve(strict=True)
                    if real_pointer != target and not real_pointer.is_relative_to(target):
                        raise ReadOnlyPolicyError(f"Git metadata pointer is outside the target repository: {path}")
                found[str(path)] = info

    for directory in dict.fromkeys((git_dir, common_dir)):
        if (directory / "objects" / "info" / "alternates").exists():
            raise ReadOnlyPolicyError("Git alternate object databases are forbidden")
        if (directory / "info" / "grafts").exists():
            raise ReadOnlyPolicyError("Git grafts are forbidden")
        if (directory / "refs" / "replace").exists():
            raise ReadOnlyPolicyError("Git replace refs are forbidden")
    return tuple(sorted((path, info.st_dev, info.st_ino, info.st_mode) for path, info in found.items()))


def _execution_capable_git_key(key: str, value: str = "") -> bool:
    key = key.lower()
    return (
        key.startswith("filter.")
        or key.startswith("diff.") and key.endswith((".command", ".textconv"))
        or key in {"core.fsmonitor", "core.hookspath", "include.path"}
        or key.startswith("includeif.")
        or key == "extensions.worktreeconfig" and value.strip().lower() in {"true", "yes", "on", "1"}
    )


def _resolve_trusted_git() -> dict[str, str]:
    """Select, validate, and describe the trusted Git executable."""
    if sys.platform == "darwin":
        defaults = ("/usr/bin/git", "/opt/homebrew/bin/git", "/usr/local/bin/git", "/opt/local/bin/git")
    elif sys.platform.startswith("linux"):
        defaults = (
            "/usr/bin/git", "/usr/local/bin/git", "/bin/git", "/opt/local/bin/git",
            "/nix/var/nix/profiles/default/bin/git", "/run/current-system/sw/bin/git",
        )
    else:
        defaults = ("/usr/bin/git",)

    approved_owners = {0}
    if sys.platform == "darwin":
        try:
            approved_owners.add(pwd.getpwnam("_installd").pw_uid)
        except KeyError:
            pass

    def safe(path: Path, info: os.stat_result) -> bool:
        if info.st_uid not in approved_owners:
            return False
        if info.st_mode & 0o022 or os.access(path, os.W_OK):
            return False
        for directory in path.parents:
            parent = directory.stat()
            if parent.st_uid not in approved_owners or parent.st_mode & 0o022 or os.access(directory, os.W_OK):
                return False
        return True

    def describe(candidate: str, source: str) -> dict[str, str] | None:
        path = Path(candidate)
        if not path.is_absolute():
            return None
        try:
            path = path.resolve(strict=True)
            info = path.stat()
            if not stat.S_ISREG(info.st_mode) or not info.st_mode & 0o111 or not safe(path, info):
                return None
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                opened = os.fstat(fd)
                identity = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
                if not stat.S_ISREG(opened.st_mode) or identity != (
                    opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns
                ):
                    return None
                with os.fdopen(fd, "rb", closefd=False) as handle:
                    content = handle.read()
                current = path.stat()
                if identity != (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns):
                    return None
            finally:
                os.close(fd)
            # The validated file and its parent chain cannot be changed by the
            # caller between this digest and execution.
            version_env = {
                "PATH": os.defpath, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_NO_REPLACE_OBJECTS": "1", "GIT_CONFIG_COUNT": str(len(_ENFORCED_GIT_CONFIG)),
            }
            for index, (key, value) in enumerate(_ENFORCED_GIT_CONFIG):
                version_env[f"GIT_CONFIG_KEY_{index}"] = key
                version_env[f"GIT_CONFIG_VALUE_{index}"] = value
            version = subprocess.run(
                [str(path), "--version"], stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10, check=False,
                env=version_env,
            )
            if version.returncode != 0 or not version.stdout.strip():
                return None
            return {
                "git_executable": str(path), "git_source": source,
                "git_owner": pwd.getpwuid(info.st_uid).pw_name,
                "git_mode": f"{stat.S_IMODE(info.st_mode):04o}",
                "git_sha256": hashlib.sha256(content).hexdigest(),
                "git_version": version.stdout.strip(),
            }
        except (OSError, subprocess.SubprocessError, KeyError):
            return None

    if _GIT_EXECUTABLE_ENV in os.environ:
        selected = describe(os.environ[_GIT_EXECUTABLE_ENV], "environment")
        if selected is None:
            raise ReadOnlyPolicyError(
                f"trusted Git executable rejected; check {_GIT_EXECUTABLE_ENV}. "
                "It must be root/installer-owned and caller-unwritable; PATH lookup is disabled."
            )
        return selected

    try:
        config_info = _GIT_CONFIG_PATH.stat()
    except FileNotFoundError:
        config_info = None
    except OSError as exc:
        raise ReadOnlyPolicyError(f"trusted Git config unavailable; check {_GIT_EXECUTABLE_ENV}") from exc
    if config_info is not None:
        try:
            config_path = _GIT_CONFIG_PATH.resolve(strict=True)
            if not stat.S_ISREG(config_info.st_mode) or not safe(config_path, config_info):
                raise ReadOnlyPolicyError(f"untrusted Git config; check {_GIT_EXECUTABLE_ENV}")
            candidate = config_path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError) as exc:
            raise ReadOnlyPolicyError(f"cannot read Git config; check {_GIT_EXECUTABLE_ENV}") from exc
        selected = describe(candidate, "config")
        if selected is None:
            raise ReadOnlyPolicyError(f"Git config executable rejected; check {_GIT_EXECUTABLE_ENV}")
        return selected

    for candidate in defaults:
        selected = describe(candidate, "default")
        if selected is not None:
            return selected
    raise ReadOnlyPolicyError(
        f"Trusted Git executable unavailable; configure {_GIT_EXECUTABLE_ENV} or install Git "
        "in a supported system location. PATH lookup is disabled."
    )


def _manifest(root: Path, root_fd: int | None = None) -> dict[str, str]:
    result: dict[str, str] = {}
    hashed_bytes = 0

    def admit(size: int) -> None:
        nonlocal hashed_bytes
        hashed_bytes += size
        if len(result) >= _MAX_MANIFEST_ENTRIES:
            raise ReadOnlyPolicyError(
                f"target exceeds G2 manifest limit of {_MAX_MANIFEST_ENTRIES} entries"
            )
        if hashed_bytes > _MAX_MANIFEST_BYTES:
            raise ReadOnlyPolicyError(
                f"target exceeds G2 manifest limit of {_MAX_MANIFEST_BYTES} file bytes"
            )

    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY) if root_fd is None else os.dup(root_fd)

    def visit(directory_fd: int, prefix: tuple[str, ...] = ()) -> None:
        for name in sorted(os.listdir(directory_fd)):
            if not prefix and name == ".git":
                continue
            rel = "/".join((*prefix, name))
            info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            mode = stat.S_IMODE(info.st_mode)
            admit(info.st_size if stat.S_ISREG(info.st_mode) else 0)
            if stat.S_ISLNK(info.st_mode):
                result[rel] = f"L:{mode:o}:{os.readlink(name, dir_fd=directory_fd)}"
                continue
            if stat.S_ISDIR(info.st_mode):
                result[rel] = f"D:{mode:o}"
                child_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd)
                try:
                    opened = os.fstat(child_fd)
                    if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                        raise ReadOnlyPolicyError("target changed during G2 manifest scan")
                    visit(child_fd, (*prefix, name))
                finally:
                    os.close(child_fd)
                continue
            if stat.S_ISREG(info.st_mode):
                digest = hashlib.sha256()
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
                try:
                    opened = os.fstat(fd)
                    if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                        raise ReadOnlyPolicyError("target changed during G2 manifest scan")
                    with os.fdopen(fd, "rb", closefd=False) as handle:
                        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                            digest.update(chunk)
                finally:
                    os.close(fd)
                result[rel] = f"F:{mode:o}:{info.st_size}:{digest.hexdigest()}"
                continue
            result[rel] = f"S:{mode:o}:{stat.S_IFMT(info.st_mode):o}"

    try:
        visit(root_fd)
    finally:
        os.close(root_fd)
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
        # Direct construction would skip bind()'s checks and use an unresolved identity.
        raise ReadOnlyPolicyError(
            "LocalReadOnlyExecutor must be created with LocalReadOnlyExecutor.bind(target_root, scratch_root)"
        )

    @classmethod
    def bind(cls, target_root: str | os.PathLike[str], scratch_root: str | os.PathLike[str]) -> "LocalReadOnlyExecutor":
        target = Path(target_root).expanduser().resolve(strict=True)
        target_info = os.stat(target, follow_symlinks=False)
        if not target.is_dir():
            raise ReadOnlyPolicyError("target_root must be a directory")

        scratch = Path(scratch_root).expanduser().resolve(strict=False)
        if scratch == target or scratch.is_relative_to(target) or target.is_relative_to(scratch):
            raise ReadOnlyPolicyError("scratch_root must be physically disjoint from target_root")
        scratch.mkdir(parents=True, exist_ok=True)
        scratch_info = os.stat(scratch, follow_symlinks=False)
        scratch = scratch.resolve(strict=True)
        if scratch == target or scratch.is_relative_to(target) or target.is_relative_to(scratch):
            raise ReadOnlyPolicyError("scratch_root must be physically disjoint from target_root")

        executor = cls.__new__(cls)
        executor.target_root = target
        executor.scratch_root = scratch
        git_info = _resolve_trusted_git()
        executor.git_executable = git_info["git_executable"]
        executor.git_source = git_info["git_source"]
        executor.git_owner = git_info["git_owner"]
        executor.git_mode = git_info["git_mode"]
        executor.git_sha256 = git_info["git_sha256"]
        executor.git_version = git_info["git_version"]
        executor._target_fd = open_directory(target, target_info)
        executor._scratch_fd = open_directory(scratch, scratch_info)
        git_entry = target / ".git"
        try:
            entry_info = os.lstat(git_entry)
        except OSError as exc:
            raise ReadOnlyPolicyError("Git metadata entry is unavailable") from exc
        if stat.S_ISLNK(entry_info.st_mode):
            raise ReadOnlyPolicyError(f"Git metadata symlink is forbidden: {git_entry}")
        if stat.S_ISDIR(entry_info.st_mode):
            initial_git_dir = git_entry
        elif stat.S_ISREG(entry_info.st_mode):
            pointer = git_entry.read_text(encoding="utf-8").strip()
            if not pointer.startswith("gitdir: "):
                raise ReadOnlyPolicyError("Git worktree pointer is invalid")
            raw_git_dir = Path(pointer[8:])
            initial_git_dir = raw_git_dir if raw_git_dir.is_absolute() else target / raw_git_dir
            initial_git_dir = initial_git_dir.resolve(strict=True)
        else:
            raise ReadOnlyPolicyError("Git metadata entry is invalid")
        common_file = initial_git_dir / "commondir"
        if common_file.exists():
            raw_common = Path(common_file.read_text(encoding="utf-8").strip())
            initial_common = raw_common if raw_common.is_absolute() else initial_git_dir / raw_common
            initial_common = initial_common.resolve(strict=True)
        else:
            initial_common = initial_git_dir
        executor._git_dirs = (initial_git_dir.resolve(strict=True), initial_common)
        executor._git_metadata = _git_metadata_fingerprint(target, *executor._git_dirs)
        digest = hashlib.sha256(str(target).encode("utf-8")).hexdigest()[:16]
        executor._identity = f"local-readonly-v1:{digest}"

        top = executor._git("rev-parse", "--show-toplevel").strip()
        if Path(top).resolve(strict=True) != target:
            raise ReadOnlyPolicyError("target_root must be the exact Git worktree root")

        if self_replace_refs := executor._git("for-each-ref", "--format=%(refname)", "refs/replace").strip():
            raise ReadOnlyPolicyError(f"Git replace refs are forbidden: {self_replace_refs}")
        executor.git_replace_ref_check_passed = True

        discovered_git_raw = executor._git("rev-parse", "--git-dir").strip()
        discovered_git = Path(discovered_git_raw)
        if not discovered_git.is_absolute():
            discovered_git = target / discovered_git
        discovered_git_dir = Path(executor._git("rev-parse", "--absolute-git-dir").strip()).resolve(strict=True)
        common_raw = executor._git("rev-parse", "--git-common-dir").strip()
        discovered_common = (Path(common_raw) if Path(common_raw).is_absolute() else target / common_raw).resolve(strict=True)
        if (discovered_git.resolve(strict=True), discovered_git_dir, discovered_common) != (
            executor._git_dirs[0], executor._git_dirs[0], executor._git_dirs[1]
        ):
            raise ReadOnlyPolicyError("Git metadata directories changed during bind")
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
        rel = path.relative_to(self.target_root).as_posix()
        fd = open_path(self._target_fd, Path(rel).parts)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise ReadOnlyPolicyError("repo.read path must be a regular file")
            if info.st_size > _MAX_READ_BYTES:
                raise ReadOnlyPolicyError("repo.read file exceeds G2 size limit")
            with os.fdopen(fd, "rb", closefd=False) as handle:
                data = handle.read(_MAX_READ_BYTES + 1)
        finally:
            os.close(fd)
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
            rel = path.relative_to(self.target_root).as_posix()
            try:
                fd = open_path(self._target_fd, Path(rel).parts)
                try:
                    info = os.fstat(fd)
                    if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_READ_BYTES:
                        continue
                    with os.fdopen(fd, "rb", closefd=False) as handle:
                        lines = handle.read(_MAX_READ_BYTES + 1).decode("utf-8").splitlines()
                finally:
                    os.close(fd)
            except (UnicodeDecodeError, OSError, ValueError):
                continue
            for line_no, line in enumerate(lines, start=1):
                if query in line:
                    matches.append(
                        {
                            "path": rel,
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
            rel = path.relative_to(self.target_root).as_posix()
            fd = open_path(self._target_fd, Path(rel).parts)
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode):
                    raise ReadOnlyPolicyError("test.discover candidate is not a regular file")
                with os.fdopen(fd, "rb", closefd=False) as handle:
                    data = handle.read(_MAX_READ_BYTES + 1)
            finally:
                os.close(fd)
            if len(data) > _MAX_READ_BYTES:
                parse_errors.append({"path": rel, "error": "size limit"})
                continue
            files.append(rel)
            try:
                tree = ast.parse(data.decode("utf-8"), filename=rel)
            except (UnicodeDecodeError, SyntaxError) as exc:
                parse_errors.append({"path": rel, "error": f"{type(exc).__name__}: {exc}"})
                continue
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
                    tests.append(f"{rel}::{node.name}")
                elif isinstance(node, ast.ClassDef):
                    is_unittest_case = any(
                        (isinstance(base, ast.Name) and base.id == "TestCase")
                        or (isinstance(base, ast.Attribute) and base.attr == "TestCase")
                        for base in node.bases
                    )
                    if node.name.startswith("Test") or is_unittest_case:
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
        rel = path.relative_to(self.scratch_root)
        parent_fd, final_name = open_parent(self._scratch_fd, rel.parts, create=True)
        temp_name = f".artifact-{os.getpid()}-{time.monotonic_ns()}.tmp"
        fd = os.open(temp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent_fd)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.rename(temp_name, final_name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            os.fsync(parent_fd)
        except Exception:
            try:
                os.unlink(temp_name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
            raise
        finally:
            os.close(parent_fd)
        rel_text = rel.as_posix()
        return {"path": rel_text, "bytes": len(data), "sha256": _sha256_bytes(data)}, f"scratch:{rel_text}"

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
        entries = _manifest(self.target_root, self._target_fd)
        return TargetSnapshot(
            head=head,
            tree=tree,
            git_status=status_text,
            manifest_hash=_manifest_hash(entries),
            manifest_entries=entries,
            git_executable=self.git_executable,
            git_source=self.git_source,
            git_owner=self.git_owner,
            git_mode=self.git_mode,
            git_sha256=self.git_sha256,
            git_version=self.git_version,
            git_replace_ref_check_passed=self.git_replace_ref_check_passed,
        )

    def _check_execution_capable_git_config(self) -> None:
        """Parse every local/worktree config without following include directives."""
        env = {key: os.environ[key] for key in ("LANG", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR") if key in os.environ}
        env.update({
            "PATH": os.defpath,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_CONFIG_COUNT": str(len(_ENFORCED_GIT_CONFIG)),
        })
        for index, (key, value) in enumerate(_ENFORCED_GIT_CONFIG):
            env[f"GIT_CONFIG_KEY_{index}"] = key
            env[f"GIT_CONFIG_VALUE_{index}"] = value
        roots = _git_metadata_roots(self.target_root, *self._git_dirs)
        config_files: set[Path] = set()
        for root in roots:
            for current, _, files in os.walk(root, followlinks=False):
                for name in files:
                    if name in {"config", "config.worktree"}:
                        config_files.add(Path(current) / name)
        for config in sorted(config_files):
            try:
                proc = subprocess.run(
                    [self.git_executable, "config", "--no-includes", "--file", str(config), "--list"],
                    cwd=self.target_root,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=10,
                    check=False,
                    env=env,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise ReadOnlyPolicyError("cannot inspect Git config") from exc
            if proc.returncode:
                raise ReadOnlyPolicyError(f"cannot inspect Git config: {config}")
            for entry in proc.stdout.splitlines():
                key, separator, value = entry.partition("=")
                if _execution_capable_git_key(key, value if separator else ""):
                    raise ReadOnlyPolicyError(f"untrusted Git config: {key.lower()}")

    def _git(self, *args: str, env_overrides: Mapping[str, str] | None = None) -> str:
        python_path = Path(sys.executable)
        if (not python_path.is_absolute() or python_path.name == "env" or " " in sys.executable
                or not python_path.is_file() or not os.access(python_path, os.X_OK)):
            raise ReadOnlyPolicyError("trusted Git launcher requires an absolute Python executable")
        before = _git_metadata_fingerprint(self.target_root, *self._git_dirs)
        self._check_execution_capable_git_config()
        env = {key: os.environ[key] for key in ("LANG", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR") if key in os.environ}
        enforced = _ENFORCED_GIT_CONFIG
        env.update(
            {
                "PATH": os.defpath,
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_COUNT": str(len(enforced)),
                "GIT_NO_REPLACE_OBJECTS": "1",
                "GIT_OPTIONAL_LOCKS": "0",
                "GIT_PAGER": "cat",
                "PAGER": "cat",
            }
        )
        for index, (key, value) in enumerate(enforced):
            env[f"GIT_CONFIG_KEY_{index}"] = key
            env[f"GIT_CONFIG_VALUE_{index}"] = value
        if env_overrides:
            env.update(env_overrides)
        if not hasattr(os, "fchdir") or not sys.executable:
            raise ReadOnlyPolicyError("pinned-directory Git execution is unavailable on this platform")
        launcher = "import os,sys; os.fchdir(int(sys.argv[1])); os.execve(sys.argv[2], sys.argv[2:], os.environ)"
        git_args = [
            self.git_executable,
            "-c", "core.fsmonitor=false",
            "-c", "core.hooksPath=/dev/null",
            "-c", "diff.external=",
            "-c", "diff.textconv=",
            *args,
        ]
        if args and args[0] == "diff":
            git_args[git_args.index("diff") + 1 : git_args.index("diff") + 1] = ["--no-ext-diff", "--no-textconv"]
        try:
            proc = subprocess.run(
                [
                    sys.executable, "-I", "-S", "-c", launcher,
                    str(self._target_fd), *git_args,
                ],
                pass_fds=(self._target_fd,),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
                check=False,
            )
        finally:
            self._check_execution_capable_git_config()
            after = _git_metadata_fingerprint(self.target_root, *self._git_dirs)
            if after != before:
                raise ReadOnlyPolicyError("Git metadata changed during Git command")
        if proc.returncode != 0:
            raise ReadOnlyPolicyError(
                f"git {' '.join(args)} failed with exit {proc.returncode}: {proc.stderr.strip()}"
            )
        return proc.stdout
