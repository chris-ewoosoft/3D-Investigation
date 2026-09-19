from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class DataClassification(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    RESTRICTED = "restricted"
    REGULATED = "regulated"


class ExecutionOrigin(StrEnum):
    LOCAL = "local"
    REMOTE_A2A = "remote_a2a"


@dataclass(frozen=True, slots=True)
class Principal:
    subject: str
    scopes: frozenset[str] = frozenset()
    classification: DataClassification = DataClassification.INTERNAL

    def permits(self, required_scope: str | None) -> bool:
        return required_scope is None or required_scope in self.scopes
