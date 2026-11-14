"""Small descriptor-relative helpers for paths below a pinned directory."""
from __future__ import annotations

import os
from pathlib import Path
import stat
from typing import Sequence


def open_directory(path: Path, expected: os.stat_result | None = None) -> int:
    """Open a validated directory and reject replacement between stat and open."""
    expected = expected or os.stat(path, follow_symlinks=False)
    if not stat.S_ISDIR(expected.st_mode):
        raise NotADirectoryError(os.fspath(path))
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (expected.st_dev, expected.st_ino):
            raise OSError("directory changed while being opened")
        return fd
    except Exception:
        os.close(fd)
        raise


def open_parent(root_fd: int, parts: Sequence[str], *, create: bool = False) -> tuple[int, str]:
    if not parts or any(part in ("", ".", "..") or "/" in part for part in parts):
        raise ValueError("unsafe relative path")
    current = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            if create:
                try:
                    os.mkdir(part, dir_fd=current)
                except FileExistsError:
                    pass
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            os.close(current)
            current = next_fd
        return current, parts[-1]
    except Exception:
        os.close(current)
        raise


def open_path(root_fd: int, parts: Sequence[str], flags: int = os.O_RDONLY, *, create_parents: bool = False, mode: int = 0o600) -> int:
    parent_fd, name = open_parent(root_fd, parts, create=create_parents)
    try:
        return os.open(name, flags | os.O_NOFOLLOW, mode, dir_fd=parent_fd)
    finally:
        os.close(parent_fd)
