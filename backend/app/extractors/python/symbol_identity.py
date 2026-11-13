"""Generate deterministic, project-relative IDs for Python declarations."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath

from app.extractors.python.exceptions import (
    InvalidGeneratedSymbolIdError,
    InvalidIdentitySchemaVersionError,
    InvalidQualifiedNameResultError,
    InvalidSymbolDeclarationError,
    InvalidSymbolFilePathError,
    SymbolIdentitySerializationError,
    SymbolIdCollisionError,
)
from app.extractors.python.identity_models import (
    PythonSymbolIdentityGenerationResult,
    PythonSymbolIdentityGenerationStatus,
    PythonSymbolIdentityRecord,
    SymbolSourceLocation,
)
from app.extractors.python.naming_models import (
    ExtractionSourceType,
    QualifiedDeclaration,
    QualifiedDeclarationType,
    QualifiedNameGenerationResult,
    QualifiedNameGenerationStatus,
)


IDENTITY_SCHEMA_VERSION = 1
PYTHON_LANGUAGE = "python"
_SUPPORTED_DECLARATION_TYPES = frozenset(QualifiedDeclarationType)


@dataclass(frozen=True, slots=True)
class PythonStableSymbolIdGenerator:
    """Assign SHA-256 IDs using declaration identity, not source contents."""

    schema_version: int = IDENTITY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version < 1:
            raise InvalidIdentitySchemaVersionError(
                "schema_version must be a positive integer."
            )

    def generate(
        self,
        *,
        qualified_name_result: QualifiedNameGenerationResult,
    ) -> PythonSymbolIdentityGenerationResult:
        """Generate one identity per incoming qualified declaration."""
        self._validate_result(qualified_name_result)
        file_path = self._normalize_relative_file_path(
            qualified_name_result.file_path
        )

        occurrence_counts: dict[
            tuple[str, QualifiedDeclarationType, str], int
        ] = {}
        seen_ids: set[str] = set()
        records: list[PythonSymbolIdentityRecord] = []

        for source_order, declaration in enumerate(
            qualified_name_result.declarations
        ):
            self._validate_declaration(
                declaration=declaration,
                source_order=source_order,
                canonical_file_path=file_path,
            )
            group_key = (
                file_path,
                declaration.declaration_type,
                declaration.qualified_name,
            )
            occurrence_index = occurrence_counts.get(group_key, 0)
            occurrence_counts[group_key] = occurrence_index + 1

            identity_key = self._build_identity_key(
                file_path=file_path,
                declaration=declaration,
                occurrence_index=occurrence_index,
            )
            symbol_id = self._create_symbol_id(identity_key)
            if symbol_id in seen_ids:
                raise SymbolIdCollisionError(
                    "Distinct declarations generated the same symbol ID."
                )
            seen_ids.add(symbol_id)

            records.append(
                PythonSymbolIdentityRecord(
                    symbol_id=symbol_id,
                    identity_schema_version=self.schema_version,
                    language=PYTHON_LANGUAGE,
                    file_path=file_path,
                    original_name=declaration.original_name,
                    qualified_name=declaration.qualified_name,
                    declaration_type=declaration.declaration_type,
                    occurrence_index=occurrence_index,
                    source_location=SymbolSourceLocation(
                        start_line=declaration.start_line,
                        definition_line=declaration.definition_line,
                        end_line=declaration.end_line,
                        start_column=declaration.start_column,
                        end_column=declaration.end_column,
                    ),
                    extraction_source_type=declaration.extraction_source_type,
                    source_order=source_order,
                )
            )

        return PythonSymbolIdentityGenerationResult(
            file_path=file_path,
            identity_schema_version=self.schema_version,
            language=PYTHON_LANGUAGE,
            status=PythonSymbolIdentityGenerationStatus.COMPLETE,
            symbols=tuple(records),
        )

    def _build_identity_key(
        self,
        *,
        file_path: str,
        declaration: QualifiedDeclaration,
        occurrence_index: int,
    ) -> dict[str, object]:
        """Build the complete versioned identity input before serialization."""
        return {
            "schema_version": self.schema_version,
            "language": PYTHON_LANGUAGE,
            "file_path": file_path,
            "declaration_type": declaration.declaration_type.name,
            "qualified_name": declaration.qualified_name,
            "occurrence_index": occurrence_index,
        }

    @staticmethod
    def _serialize_identity_key(identity_key: dict[str, object]) -> bytes:
        """Serialize identity fields using canonical compact UTF-8 JSON."""
        try:
            canonical_json = json.dumps(
                identity_key,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            return canonical_json.encode("utf-8")
        except (TypeError, ValueError, UnicodeError) as error:
            raise SymbolIdentitySerializationError(
                "Canonical symbol identity could not be encoded as UTF-8 JSON."
            ) from error

    def _create_symbol_id(self, identity_key: dict[str, object]) -> str:
        """Hash canonical identity bytes and validate the required ID format."""
        payload = self._serialize_identity_key(identity_key)
        try:
            digest = hashlib.sha256(payload).hexdigest()
        except (TypeError, ValueError) as error:
            raise SymbolIdentitySerializationError(
                "Canonical symbol identity could not be hashed."
            ) from error
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise InvalidGeneratedSymbolIdError(
                "SHA-256 must return a lowercase 64-character hexadecimal digest."
            )
        return f"py:v{self.schema_version}:{digest}"

    @classmethod
    def _normalize_relative_file_path(cls, file_path: object) -> str:
        """Normalize slash style while rejecting absolute and traversing paths."""
        if (
            not isinstance(file_path, str)
            or not file_path
            or "\x00" in file_path
        ):
            raise InvalidSymbolFilePathError(
                "file_path must be a non-empty project-relative path."
            )

        normalized = file_path.replace("\\", "/")
        windows_path = PureWindowsPath(file_path)
        posix_path = PurePosixPath(normalized)
        components = normalized.split("/")
        if (
            posix_path.is_absolute()
            or windows_path.is_absolute()
            or windows_path.drive
            or any(component in {"", ".", ".."} for component in components)
        ):
            raise InvalidSymbolFilePathError(
                "file_path must not be absolute, empty, or contain traversal."
            )
        return "/".join(components)

    @classmethod
    def _validate_result(cls, result: object) -> None:
        if not isinstance(result, QualifiedNameGenerationResult):
            raise InvalidQualifiedNameResultError(
                "qualified_name_result must be a QualifiedNameGenerationResult."
            )
        if result.status is not QualifiedNameGenerationStatus.COMPLETE:
            raise InvalidQualifiedNameResultError(
                "qualified_name_result must have complete generation status."
            )
        if not isinstance(result.declarations, tuple) or any(
            not isinstance(item, QualifiedDeclaration)
            for item in result.declarations
        ):
            raise InvalidQualifiedNameResultError(
                "qualified_name_result must contain a tuple of declarations."
            )

    @classmethod
    def _validate_declaration(
        cls,
        *,
        declaration: QualifiedDeclaration,
        source_order: int,
        canonical_file_path: str,
    ) -> None:
        declaration_path = cls._normalize_relative_file_path(
            declaration.file_path
        )
        if declaration_path != canonical_file_path:
            raise InvalidSymbolDeclarationError(
                "A declaration file path does not match the result file path."
            )
        if (
            not isinstance(declaration.declaration_type, QualifiedDeclarationType)
            or declaration.declaration_type not in _SUPPORTED_DECLARATION_TYPES
        ):
            raise InvalidSymbolDeclarationError(
                "A declaration has an unsupported declaration type."
            )
        if not isinstance(declaration.extraction_source_type, ExtractionSourceType):
            raise InvalidSymbolDeclarationError(
                "A declaration has an invalid extraction source type."
            )
        if (
            declaration.declaration_type is QualifiedDeclarationType.CLASS
        ) != (
            declaration.extraction_source_type is ExtractionSourceType.CLASS_EXTRACTOR
        ):
            raise InvalidSymbolDeclarationError(
                "Declaration type and extraction source type are inconsistent."
            )
        if (
            not isinstance(declaration.original_name, str)
            or not declaration.original_name
            or "." in declaration.original_name
        ):
            raise InvalidSymbolDeclarationError(
                "A declaration must have a non-empty, unqualified original name."
            )
        if (
            not isinstance(declaration.qualified_name, str)
            or not declaration.qualified_name
        ):
            raise InvalidSymbolDeclarationError(
                "A declaration is missing its qualified name."
            )
        components = declaration.lexical_scope_components
        if (
            not isinstance(components, tuple)
            or not components
            or any(
                not isinstance(component, str)
                or not component
                or "." in component
                for component in components
            )
            or components[-1] != declaration.original_name
            or ".".join(components) != declaration.qualified_name
        ):
            raise InvalidSymbolDeclarationError(
                "A declaration has inconsistent lexical and qualified names."
            )
        expected_enclosing_scope = (
            ".".join(components[:-1]) if len(components) > 1 else None
        )
        if declaration.enclosing_scope != expected_enclosing_scope:
            raise InvalidSymbolDeclarationError(
                "A declaration has inconsistent enclosing scope metadata."
            )
        if (
            declaration.declaration_type is QualifiedDeclarationType.FUNCTION
            and len(components) != 1
        ):
            raise InvalidSymbolDeclarationError(
                "A module-level function has an unexpected lexical scope."
            )
        if (
            declaration.declaration_type
            in {
                QualifiedDeclarationType.METHOD,
                QualifiedDeclarationType.NESTED_FUNCTION,
            }
            and len(components) < 2
        ):
            raise InvalidSymbolDeclarationError(
                "A method or nested function must have an enclosing scope."
            )
        if type(declaration.source_order) is not int or (
            declaration.source_order != source_order
        ):
            raise InvalidSymbolDeclarationError(
                "Declaration source_order must match its position in the result."
            )
        cls._validate_source_location(declaration)

    @staticmethod
    def _validate_source_location(declaration: QualifiedDeclaration) -> None:
        for name in ("start_line", "definition_line"):
            value = getattr(declaration, name)
            if type(value) is not int or value < 1:
                raise InvalidSymbolDeclarationError(
                    f"A declaration has an invalid {name}."
                )
        if declaration.definition_line < declaration.start_line:
            raise InvalidSymbolDeclarationError(
                "A declaration definition cannot precede its source start."
            )
        if declaration.end_line is not None and (
            type(declaration.end_line) is not int
            or declaration.end_line < declaration.definition_line
        ):
            raise InvalidSymbolDeclarationError(
                "A declaration has an invalid end_line."
            )
        for name in ("start_column", "end_column"):
            value = getattr(declaration, name)
            if value is not None and (type(value) is not int or value < 0):
                raise InvalidSymbolDeclarationError(
                    f"A declaration has an invalid {name}."
                )
