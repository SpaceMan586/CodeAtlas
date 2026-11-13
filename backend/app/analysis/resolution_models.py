"""Immutable JSON-safe models for call resolution results."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType

from app.ir import SourceLocationIR
from app.ir.validation import (
    JSONMetadata,
    freeze_metadata,
    normalize_relative_path,
    thaw_metadata,
)


class CallResolutionStatus(str, Enum):
    """Outcome for one syntactic call site."""

    RESOLVED = "RESOLVED"
    UNRESOLVED = "UNRESOLVED"
    AMBIGUOUS = "AMBIGUOUS"


@dataclass(frozen=True, slots=True)
class CallSiteReference:
    """Snapshot-local reference to a call, not a persistent source identity.

    Occurrence order is scoped to ``file_path``. When an input CallSiteIR has
    no occurrence order, the resolver supplies a deterministic per-file order
    from the ProjectIR call-site sequence.
    """

    file_path: str
    occurrence_order: int
    source_location: SourceLocationIR | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "file_path",
            normalize_relative_path(self.file_path, "file_path"),
        )
        if type(self.occurrence_order) is not int or self.occurrence_order < 0:
            raise ValueError("occurrence_order must be a non-negative integer.")
        if self.source_location is not None and not isinstance(
            self.source_location,
            SourceLocationIR,
        ):
            raise TypeError("source_location must be a SourceLocationIR or None.")

    def to_dict(self) -> dict[str, object]:
        return {
            "file_path": self.file_path,
            "occurrence_order": self.occurrence_order,
            "source_location": (
                self.source_location.to_dict()
                if self.source_location is not None
                else None
            ),
        }


@dataclass(frozen=True, slots=True)
class ResolutionDiagnostic:
    """Safe explanation attached to an unresolved or ambiguous result."""

    code: str
    category: str
    message: str
    file_path: str
    source_location: SourceLocationIR | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or not self.code.strip():
            raise ValueError("code must be a non-empty string.")
        if not isinstance(self.category, str) or not self.category.strip():
            raise ValueError("category must be a non-empty string.")
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError("message must be a non-empty string.")
        object.__setattr__(
            self,
            "file_path",
            normalize_relative_path(self.file_path, "file_path"),
        )
        if self.source_location is not None and not isinstance(
            self.source_location,
            SourceLocationIR,
        ):
            raise TypeError("source_location must be a SourceLocationIR or None.")

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "category": self.category,
            "message": self.message,
            "file_path": self.file_path,
            "source_location": (
                self.source_location.to_dict()
                if self.source_location is not None
                else None
            ),
        }


@dataclass(frozen=True, slots=True)
class CallResolution:
    """Resolution outcome kept separate from CallSiteIR and dependency edges."""

    call_site_reference: CallSiteReference
    status: CallResolutionStatus
    target_symbol_id: str | None = None
    candidate_symbol_ids: tuple[str, ...] = ()
    resolution_method: str | None = None
    evidence: JSONMetadata = field(default_factory=dict)
    diagnostics: tuple[ResolutionDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.call_site_reference, CallSiteReference):
            raise TypeError("call_site_reference must be a CallSiteReference.")
        if not isinstance(self.status, CallResolutionStatus):
            raise TypeError("status must be a CallResolutionStatus.")
        if self.target_symbol_id is not None and (
            not isinstance(self.target_symbol_id, str)
            or not self.target_symbol_id.strip()
        ):
            raise ValueError("target_symbol_id must be a non-empty string or None.")
        if not isinstance(self.candidate_symbol_ids, tuple) or any(
            not isinstance(item, str) or not item.strip()
            for item in self.candidate_symbol_ids
        ):
            raise ValueError("candidate_symbol_ids must be a tuple of non-empty IDs.")
        if len(self.candidate_symbol_ids) != len(set(self.candidate_symbol_ids)):
            raise ValueError("candidate_symbol_ids must not contain duplicates.")
        if self.resolution_method is not None and (
            not isinstance(self.resolution_method, str)
            or not self.resolution_method.strip()
        ):
            raise ValueError("resolution_method must be a non-empty string or None.")
        frozen_evidence = freeze_metadata(self.evidence, "evidence")
        if frozen_evidence is None:
            raise ValueError("evidence must be a JSON object.")
        object.__setattr__(self, "evidence", frozen_evidence)
        if not isinstance(self.diagnostics, tuple) or any(
            not isinstance(item, ResolutionDiagnostic) for item in self.diagnostics
        ):
            raise TypeError("diagnostics must be a tuple of ResolutionDiagnostic.")
        if any(
            item.file_path != self.call_site_reference.file_path
            for item in self.diagnostics
        ):
            raise ValueError(
                "Resolution diagnostics must reference the call-site file."
            )

        if self.status is CallResolutionStatus.RESOLVED:
            if self.target_symbol_id is None:
                raise ValueError("A resolved call must have a target symbol ID.")
            if self.target_symbol_id not in self.candidate_symbol_ids:
                raise ValueError("A resolved target must be one of its candidates.")
            if self.resolution_method is None:
                raise ValueError("A resolved call must have a resolution method.")
            if self.diagnostics:
                raise ValueError("A resolved call cannot contain failure diagnostics.")
        elif self.target_symbol_id is not None:
            raise ValueError("Only a resolved call may have a target symbol ID.")
        if (
            self.status is CallResolutionStatus.AMBIGUOUS
            and not self.candidate_symbol_ids
        ):
            raise ValueError("An ambiguous call must preserve candidate symbol IDs.")

    def to_dict(self) -> dict[str, object]:
        return {
            "call_site_reference": self.call_site_reference.to_dict(),
            "status": self.status.value,
            "target_symbol_id": self.target_symbol_id,
            "candidate_symbol_ids": list(self.candidate_symbol_ids),
            "resolution_method": self.resolution_method,
            "evidence": thaw_metadata(self.evidence),
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }


@dataclass(frozen=True, slots=True)
class CallResolutionResult:
    """Ordered resolutions and counts derived solely from their records."""

    resolutions: tuple[CallResolution, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.resolutions, tuple) or any(
            not isinstance(item, CallResolution) for item in self.resolutions
        ):
            raise TypeError("resolutions must be a tuple of CallResolution records.")
        references = [
            (
                item.call_site_reference.file_path,
                item.call_site_reference.occurrence_order,
            )
            for item in self.resolutions
        ]
        if len(references) != len(set(references)):
            raise ValueError("Call-site references must be unique within each file.")

    @property
    def total_call_sites(self) -> int:
        return len(self.resolutions)

    @property
    def resolved_count(self) -> int:
        return sum(
            item.status is CallResolutionStatus.RESOLVED
            for item in self.resolutions
        )

    @property
    def unresolved_count(self) -> int:
        return sum(
            item.status is CallResolutionStatus.UNRESOLVED
            for item in self.resolutions
        )

    @property
    def ambiguous_count(self) -> int:
        return sum(
            item.status is CallResolutionStatus.AMBIGUOUS
            for item in self.resolutions
        )

    @property
    def diagnostics(self) -> tuple[ResolutionDiagnostic, ...]:
        return tuple(
            diagnostic
            for resolution in self.resolutions
            for diagnostic in resolution.diagnostics
        )

    @property
    def counts_by_resolution_method(self) -> Mapping[str, int]:
        counts: dict[str, int] = {}
        for item in self.resolutions:
            if item.resolution_method is not None:
                counts[item.resolution_method] = (
                    counts.get(item.resolution_method, 0) + 1
                )
        return MappingProxyType(dict(sorted(counts.items())))

    def to_dict(self) -> dict[str, object]:
        return {
            "total_call_sites": self.total_call_sites,
            "resolved_count": self.resolved_count,
            "unresolved_count": self.unresolved_count,
            "ambiguous_count": self.ambiguous_count,
            "counts_by_resolution_method": dict(self.counts_by_resolution_method),
            "resolutions": [item.to_dict() for item in self.resolutions],
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }
