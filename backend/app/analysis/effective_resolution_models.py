"""Immutable effective call-resolution records combining resolver stages."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType

from app.analysis.cross_file_resolution_models import (
    CrossFileCallResolution,
    CrossFileResolutionDiagnostic,
)
from app.analysis.resolution_models import (
    CallResolution,
    CallResolutionStatus,
    CallSiteReference,
    ResolutionDiagnostic,
)
from app.ir import ProjectIR
from app.ir.validation import JSONMetadata, freeze_metadata, thaw_metadata


class ResolutionProvenance(str, Enum):
    LOCAL_RESOLUTION = "LOCAL_RESOLUTION"
    CROSS_FILE_RESOLUTION = "CROSS_FILE_RESOLUTION"


@dataclass(frozen=True, slots=True)
class EffectiveCallResolution:
    """One effective status for one original call-site reference."""

    call_site_reference: CallSiteReference
    status: CallResolutionStatus
    provenance: ResolutionProvenance
    target_symbol_id: str | None
    candidate_symbol_ids: tuple[str, ...]
    resolution_method: str | None
    evidence: JSONMetadata
    local_resolution: CallResolution
    cross_file_resolution: CrossFileCallResolution | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.call_site_reference, CallSiteReference):
            raise TypeError("call_site_reference must be a CallSiteReference.")
        if not isinstance(self.status, CallResolutionStatus):
            raise TypeError("status must be a CallResolutionStatus.")
        if not isinstance(self.provenance, ResolutionProvenance):
            raise TypeError("provenance must be a ResolutionProvenance.")
        if self.local_resolution.call_site_reference != self.call_site_reference:
            raise ValueError("local resolution must reference the effective call site.")
        if self.cross_file_resolution is not None and self.cross_file_resolution.call_site_reference != self.call_site_reference:
            raise ValueError("cross-file resolution must reference the effective call site.")
        if self.provenance is ResolutionProvenance.CROSS_FILE_RESOLUTION:
            if self.local_resolution.status is not CallResolutionStatus.UNRESOLVED:
                raise ValueError("cross-file provenance can only refine locally unresolved calls.")
            if self.cross_file_resolution is None or self.status is not self.cross_file_resolution.status:
                raise ValueError("cross-file provenance must retain the selected cross-file result.")
        elif self.status is not self.local_resolution.status:
            raise ValueError("local provenance must preserve the local status.")
        expected_target = (
            self.cross_file_resolution.target_symbol_id
            if self.provenance is ResolutionProvenance.CROSS_FILE_RESOLUTION
            else self.local_resolution.target_symbol_id
        )
        if self.target_symbol_id != expected_target:
            raise ValueError("effective target does not match its provenance record.")
        frozen = freeze_metadata(self.evidence, "evidence")
        if frozen is None:
            raise ValueError("evidence must be a JSON object.")
        object.__setattr__(self, "evidence", frozen)

    def to_dict(self) -> dict[str, object]:
        return {
            "call_site_reference": self.call_site_reference.to_dict(),
            "status": self.status.value,
            "provenance": self.provenance.value,
            "target_symbol_id": self.target_symbol_id,
            "candidate_symbol_ids": list(self.candidate_symbol_ids),
            "resolution_method": self.resolution_method,
            "evidence": thaw_metadata(self.evidence),
            "local_resolution": self.local_resolution.to_dict(),
            "cross_file_resolution": self.cross_file_resolution.to_dict() if self.cross_file_resolution else None,
        }


@dataclass(frozen=True, slots=True)
class EffectiveCallResolutionResult:
    """Exactly one effective result per ProjectIR call site."""

    records: tuple[EffectiveCallResolution, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.records, tuple) or any(not isinstance(item, EffectiveCallResolution) for item in self.records):
            raise TypeError("records must contain EffectiveCallResolution values.")
        refs = tuple((item.call_site_reference.file_path, item.call_site_reference.occurrence_order) for item in self.records)
        if len(refs) != len(set(refs)):
            raise ValueError("effective call-site references must be unique.")

    @classmethod
    def build(
        cls,
        *,
        project_ir: ProjectIR,
        local_records: tuple[CallResolution, ...],
        cross_file_records: tuple[CrossFileCallResolution, ...],
    ) -> "EffectiveCallResolutionResult":
        expected_calls = tuple(project_ir.call_sites)
        if len(local_records) != len(expected_calls):
            raise ValueError("local result count must equal ProjectIR call-site count.")
        refs = tuple(record.call_site_reference for record in local_records)
        if len(refs) != len(set(refs)):
            raise ValueError("local call-site references must be unique.")
        cross_by_ref = {record.call_site_reference: record for record in cross_file_records}
        if len(cross_by_ref) != len(cross_file_records):
            raise ValueError("cross-file call-site references must be unique.")
        if not set(cross_by_ref).issubset(refs):
            raise ValueError("cross-file results contain a call absent from local results.")
        symbol_ids = {symbol.symbol_id for symbol in project_ir.symbols}
        records: list[EffectiveCallResolution] = []
        for local in local_records:
            cross = cross_by_ref.get(local.call_site_reference)
            if cross is not None and local.status is not CallResolutionStatus.UNRESOLVED:
                raise ValueError("cross-file results may only include locally unresolved calls.")
            if local.status is not CallResolutionStatus.UNRESOLVED:
                status, provenance = local.status, ResolutionProvenance.LOCAL_RESOLUTION
                target, candidates, method, evidence = (
                    local.target_symbol_id, local.candidate_symbol_ids, local.resolution_method, dict(local.evidence)
                )
            elif cross is not None and cross.status in {CallResolutionStatus.RESOLVED, CallResolutionStatus.AMBIGUOUS}:
                status, provenance = cross.status, ResolutionProvenance.CROSS_FILE_RESOLUTION
                target, candidates, method = cross.target_symbol_id, cross.candidate_symbol_ids, cross.resolution_method
                evidence = {"local": dict(local.evidence), "cross_file": dict(cross.evidence)}
            else:
                status, provenance = CallResolutionStatus.UNRESOLVED, ResolutionProvenance.LOCAL_RESOLUTION
                target, candidates, method = None, local.candidate_symbol_ids, local.resolution_method
                evidence = {"local": dict(local.evidence), "cross_file": dict(cross.evidence) if cross else {}}
            if target is not None and target not in symbol_ids:
                raise ValueError("effective target symbol is absent from ProjectIR.")
            if not set(candidates).issubset(symbol_ids):
                raise ValueError("effective candidate symbol is absent from ProjectIR.")
            records.append(EffectiveCallResolution(
                call_site_reference=local.call_site_reference,
                status=status,
                provenance=provenance,
                target_symbol_id=target,
                candidate_symbol_ids=candidates,
                resolution_method=method,
                evidence=evidence,
                local_resolution=local,
                cross_file_resolution=cross,
            ))
        if len(records) != len(project_ir.call_sites):
            raise ValueError("effective result count must equal ProjectIR call-site count.")
        return cls(tuple(records))

    @property
    def total_call_sites(self) -> int:
        return len(self.records)

    @property
    def resolved_count(self) -> int:
        return sum(record.status is CallResolutionStatus.RESOLVED for record in self.records)

    @property
    def unresolved_count(self) -> int:
        return sum(record.status is CallResolutionStatus.UNRESOLVED for record in self.records)

    @property
    def ambiguous_count(self) -> int:
        return sum(record.status is CallResolutionStatus.AMBIGUOUS for record in self.records)

    @property
    def counts_by_resolution_method(self) -> Mapping[str, int]:
        counts: dict[str, int] = {}
        for record in self.records:
            if record.resolution_method:
                counts[record.resolution_method] = counts.get(record.resolution_method, 0) + 1
        return MappingProxyType(dict(sorted(counts.items())))

    @property
    def diagnostics(self) -> tuple[ResolutionDiagnostic | CrossFileResolutionDiagnostic, ...]:
        return tuple(
            diagnostic
            for item in self.records
            for diagnostic in (
                *item.local_resolution.diagnostics,
                *(item.cross_file_resolution.diagnostics if item.cross_file_resolution else ()),
            )
        )

    @property
    def summary(self) -> Mapping[str, int]:
        return MappingProxyType({
            "total_call_sites": self.total_call_sites,
            "resolved_count": self.resolved_count,
            "unresolved_count": self.unresolved_count,
            "ambiguous_count": self.ambiguous_count,
        })

    def to_dict(self) -> dict[str, object]:
        return {
            "summary": dict(self.summary),
            "counts_by_resolution_method": dict(self.counts_by_resolution_method),
            "records": [record.to_dict() for record in self.records],
            "diagnostics": [diagnostic.to_dict() for diagnostic in self.diagnostics],
        }
