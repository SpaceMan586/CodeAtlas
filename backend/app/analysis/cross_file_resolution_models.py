"""Immutable result models for Python cross-file call resolution."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from app.analysis.module_resolution_models import ImportReference
from app.analysis.resolution_models import CallResolutionStatus, CallSiteReference
from app.ir import SourceLocationIR
from app.ir.validation import JSONMetadata, freeze_metadata, normalize_relative_path, thaw_metadata


class CrossFileEvaluationStatus(str):
    EVALUATED = "evaluated"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True, slots=True)
class CrossFileResolutionDiagnostic:
    code: str
    category: str
    message: str
    file_path: str
    source_location: SourceLocationIR | None = None

    def __post_init__(self) -> None:
        if not self.code or not self.category or not self.message:
            raise ValueError("diagnostic code, category, and message are required.")
        object.__setattr__(self, "file_path", normalize_relative_path(self.file_path))
        if self.source_location is not None and not isinstance(self.source_location, SourceLocationIR):
            raise TypeError("source_location must be a SourceLocationIR or None.")

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "category": self.category,
            "message": self.message,
            "file_path": self.file_path,
            "source_location": self.source_location.to_dict() if self.source_location else None,
        }


@dataclass(frozen=True, slots=True)
class CrossFileCallResolution:
    call_site_reference: CallSiteReference
    status: CallResolutionStatus
    evaluation_status: str
    source_file_path: str
    target_symbol_id: str | None = None
    candidate_symbol_ids: tuple[str, ...] = ()
    target_file_path: str | None = None
    candidate_target_file_paths: tuple[str, ...] = ()
    import_references: tuple[ImportReference, ...] = ()
    resolution_method: str | None = None
    unresolved_reason: str | None = None
    evidence: JSONMetadata = field(default_factory=dict)
    diagnostics: tuple[CrossFileResolutionDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.call_site_reference, CallSiteReference):
            raise TypeError("call_site_reference must be a CallSiteReference.")
        if not isinstance(self.status, CallResolutionStatus):
            raise TypeError("status must be a CallResolutionStatus.")
        if self.evaluation_status not in {
            CrossFileEvaluationStatus.EVALUATED,
            CrossFileEvaluationStatus.NOT_APPLICABLE,
        }:
            raise ValueError("evaluation_status must be evaluated or not_applicable.")
        object.__setattr__(self, "source_file_path", normalize_relative_path(self.source_file_path))
        if self.source_file_path != self.call_site_reference.file_path:
            raise ValueError("source_file_path must match the call-site reference.")
        if self.target_symbol_id is not None and not self.target_symbol_id.strip():
            raise ValueError("target_symbol_id must be non-empty or None.")
        if not isinstance(self.candidate_symbol_ids, tuple) or any(not item for item in self.candidate_symbol_ids):
            raise ValueError("candidate_symbol_ids must be a tuple of non-empty IDs.")
        if len(self.candidate_symbol_ids) != len(set(self.candidate_symbol_ids)):
            raise ValueError("candidate_symbol_ids must not contain duplicates.")
        if self.target_file_path is not None:
            object.__setattr__(self, "target_file_path", normalize_relative_path(self.target_file_path))
        normalized_paths = tuple(normalize_relative_path(item) for item in self.candidate_target_file_paths)
        if len(normalized_paths) != len(set(normalized_paths)):
            raise ValueError("candidate_target_file_paths must not contain duplicates.")
        object.__setattr__(self, "candidate_target_file_paths", normalized_paths)
        if not isinstance(self.import_references, tuple) or any(not isinstance(item, ImportReference) for item in self.import_references):
            raise TypeError("import_references must contain ImportReference values.")
        if len(set((item.file_path, item.record_order) for item in self.import_references)) != len(self.import_references):
            raise ValueError("import_references must be unique.")
        if self.resolution_method is not None and not self.resolution_method:
            raise ValueError("resolution_method must be non-empty or None.")
        if self.unresolved_reason is not None and not self.unresolved_reason:
            raise ValueError("unresolved_reason must be non-empty or None.")
        evidence = freeze_metadata(self.evidence, "evidence")
        if evidence is None:
            raise ValueError("evidence must be a JSON object.")
        object.__setattr__(self, "evidence", evidence)
        if not isinstance(self.diagnostics, tuple) or any(not isinstance(item, CrossFileResolutionDiagnostic) for item in self.diagnostics):
            raise TypeError("diagnostics must contain CrossFileResolutionDiagnostic values.")
        if any(item.file_path != self.source_file_path for item in self.diagnostics):
            raise ValueError("diagnostics must reference the call-site file.")
        if self.status is CallResolutionStatus.RESOLVED:
            if self.target_symbol_id is None or self.target_file_path is None:
                raise ValueError("a resolved cross-file call needs a target symbol and file.")
            if self.target_symbol_id not in self.candidate_symbol_ids:
                raise ValueError("target_symbol_id must appear in candidate_symbol_ids.")
            if self.target_file_path == self.source_file_path:
                raise ValueError("cross-file targets must be in a different source file.")
            if self.evaluation_status != CrossFileEvaluationStatus.EVALUATED:
                raise ValueError("only evaluated calls can be resolved.")
            if self.diagnostics:
                raise ValueError("resolved calls cannot contain failure diagnostics.")
        elif self.target_symbol_id is not None or self.target_file_path is not None:
            raise ValueError("only resolved calls may contain a unique target.")
        if self.status is CallResolutionStatus.AMBIGUOUS and not self.candidate_symbol_ids:
            raise ValueError("ambiguous calls must preserve candidate symbol IDs.")
        if self.evaluation_status == CrossFileEvaluationStatus.NOT_APPLICABLE:
            if self.status is not CallResolutionStatus.UNRESOLVED or self.resolution_method is not None:
                raise ValueError("not-applicable calls must remain unresolved without a method.")

    def to_dict(self) -> dict[str, object]:
        return {
            "call_site_reference": self.call_site_reference.to_dict(),
            "status": self.status.value,
            "evaluation_status": self.evaluation_status,
            "source_file_path": self.source_file_path,
            "target_symbol_id": self.target_symbol_id,
            "candidate_symbol_ids": list(self.candidate_symbol_ids),
            "target_file_path": self.target_file_path,
            "candidate_target_file_paths": list(self.candidate_target_file_paths),
            "import_references": [item.to_dict() for item in self.import_references],
            "resolution_method": self.resolution_method,
            "unresolved_reason": self.unresolved_reason,
            "evidence": thaw_metadata(self.evidence),
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }


@dataclass(frozen=True, slots=True)
class CrossFileCallResolutionResult:
    records: tuple[CrossFileCallResolution, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.records, tuple) or any(not isinstance(item, CrossFileCallResolution) for item in self.records):
            raise TypeError("records must contain CrossFileCallResolution values.")
        refs = [(item.call_site_reference.file_path, item.call_site_reference.occurrence_order) for item in self.records]
        if len(refs) != len(set(refs)):
            raise ValueError("cross-file call references must be unique.")

    @property
    def evaluated_call_count(self) -> int:
        return sum(item.evaluation_status == CrossFileEvaluationStatus.EVALUATED for item in self.records)

    @property
    def not_applicable_count(self) -> int:
        return sum(item.evaluation_status == CrossFileEvaluationStatus.NOT_APPLICABLE for item in self.records)

    @property
    def resolved_count(self) -> int:
        return sum(item.status is CallResolutionStatus.RESOLVED for item in self.records)

    @property
    def unresolved_count(self) -> int:
        return sum(item.status is CallResolutionStatus.UNRESOLVED for item in self.records)

    @property
    def ambiguous_count(self) -> int:
        return sum(item.status is CallResolutionStatus.AMBIGUOUS for item in self.records)

    @property
    def diagnostics(self) -> tuple[CrossFileResolutionDiagnostic, ...]:
        return tuple(diagnostic for item in self.records for diagnostic in item.diagnostics)

    @property
    def counts_by_resolution_method(self) -> Mapping[str, int]:
        counts: dict[str, int] = {}
        for record in self.records:
            if record.resolution_method is not None:
                counts[record.resolution_method] = counts.get(record.resolution_method, 0) + 1
        return MappingProxyType(dict(sorted(counts.items())))

    @property
    def counts_by_source_target_file_pair(self) -> Mapping[str, int]:
        counts: dict[str, int] = {}
        for record in self.records:
            targets = record.candidate_target_file_paths or (
                (record.target_file_path,) if record.target_file_path else ()
            )
            for target in targets:
                key = f"{record.source_file_path} -> {target}"
                counts[key] = counts.get(key, 0) + 1
        return MappingProxyType(dict(sorted(counts.items())))

    @property
    def summary(self) -> Mapping[str, int]:
        return MappingProxyType({
            "evaluated_call_count": self.evaluated_call_count,
            "not_applicable_count": self.not_applicable_count,
            "resolved_count": self.resolved_count,
            "unresolved_count": self.unresolved_count,
            "ambiguous_count": self.ambiguous_count,
        })

    def to_dict(self) -> dict[str, object]:
        return {
            "summary": dict(self.summary),
            "counts_by_resolution_method": dict(self.counts_by_resolution_method),
            "counts_by_source_target_file_pair": dict(self.counts_by_source_target_file_pair),
            "records": [item.to_dict() for item in self.records],
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }
