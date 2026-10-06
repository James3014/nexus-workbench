from __future__ import annotations

from typing import Protocol, runtime_checkable

from .model import ActionCell, ObservationBundle


@runtime_checkable
class Executor(Protocol):
    """Physical execution adapter. This interface carries no Nexus authority."""

    @property
    def identity(self) -> str: ...

    def execute(self, action: ActionCell) -> ObservationBundle: ...
