"""Generate lexical qualified names from function and class extraction records."""

from pathlib import PurePosixPath, PureWindowsPath

from app.extractors.python.exceptions import (
    InvalidClassExtractionResultError,
    InvalidDeclarationMetadataError,
    InvalidFunctionExtractionResultError,
    MismatchedExtractionFileError,
)
from app.extractors.python.models import (
    ClassDeclaration,
    ClassDeclarationType,
    ClassExtractionResult,
    ClassExtractionStatus,
    ClassParentScopeType,
    DeclarationType,
    ExtractionStatus,
    FunctionDeclaration,
    PythonFunctionExtractionResult,
)
from app.extractors.python.naming_models import (
    ExtractionSourceType,
    QualifiedDeclaration,
    QualifiedDeclarationType,
    QualifiedNameGenerationResult,
    QualifiedNameGenerationStatus,
)


class PythonQualifiedNameGenerator:
    """Normalize existing function and class scope paths into lexical names."""

    def generate(
        self,
        *,
        function_result: PythonFunctionExtractionResult,
        class_result: ClassExtractionResult,
    ) -> QualifiedNameGenerationResult:
        """Combine extraction records from one file in source declaration order."""
        self._validate_function_result(function_result)
        self._validate_class_result(class_result)
        if function_result.file_path != class_result.file_path:
            raise MismatchedExtractionFileError(
                "Function and class extraction results must refer to the same file."
            )
        self._validate_relative_file_path(function_result.file_path)

        candidates: list[tuple[tuple[int, int, int], int, QualifiedDeclaration]] = []
        input_order = 0
        for declaration in function_result.declarations:
            self._validate_declaration_file_path(
                declaration.file_path,
                function_result.file_path,
            )
            scope_components = self._function_scope(declaration)
            qualified = self._qualified_function(
                file_path=function_result.file_path,
                declaration=declaration,
                scope_components=scope_components,
            )
            candidates.append(
                (self._source_position(qualified), input_order, qualified)
            )
            input_order += 1

        for declaration in class_result.declarations:
            self._validate_declaration_file_path(
                declaration.file_path,
                class_result.file_path,
            )
            scope_components = self._class_scope(declaration)
            qualified = self._qualified_class(
                file_path=class_result.file_path,
                declaration=declaration,
                scope_components=scope_components,
            )
            candidates.append(
                (self._source_position(qualified), input_order, qualified)
            )
            input_order += 1

        candidates.sort(key=lambda candidate: (candidate[0], candidate[1]))
        declarations = tuple(
            QualifiedDeclaration(
                file_path=item.file_path,
                original_name=item.original_name,
                qualified_name=item.qualified_name,
                declaration_type=item.declaration_type,
                enclosing_scope=item.enclosing_scope,
                lexical_scope_components=item.lexical_scope_components,
                extraction_source_type=item.extraction_source_type,
                start_line=item.start_line,
                definition_line=item.definition_line,
                end_line=item.end_line,
                start_column=item.start_column,
                end_column=item.end_column,
                source_order=source_order,
            )
            for source_order, (_, _, item) in enumerate(candidates)
        )
        return QualifiedNameGenerationResult(
            file_path=function_result.file_path,
            status=QualifiedNameGenerationStatus.COMPLETE,
            declarations=declarations,
        )

    @staticmethod
    def _validate_function_result(result: object) -> None:
        if not isinstance(result, PythonFunctionExtractionResult):
            raise InvalidFunctionExtractionResultError(
                "function_result must be a PythonFunctionExtractionResult."
            )
        if result.status is not ExtractionStatus.COMPLETE:
            raise InvalidFunctionExtractionResultError(
                "function_result must have complete extraction status."
            )
        if not isinstance(result.declarations, tuple) or any(
            not isinstance(item, FunctionDeclaration)
            for item in result.declarations
        ):
            raise InvalidFunctionExtractionResultError(
                "function_result contains invalid declaration records."
            )

    @staticmethod
    def _validate_class_result(result: object) -> None:
        if not isinstance(result, ClassExtractionResult):
            raise InvalidClassExtractionResultError(
                "class_result must be a ClassExtractionResult."
            )
        if result.status is not ClassExtractionStatus.COMPLETE:
            raise InvalidClassExtractionResultError(
                "class_result must have complete extraction status."
            )
        if not isinstance(result.declarations, tuple) or any(
            not isinstance(item, ClassDeclaration)
            for item in result.declarations
        ):
            raise InvalidClassExtractionResultError(
                "class_result contains invalid declaration records."
            )

    @staticmethod
    def _validate_relative_file_path(file_path: object) -> None:
        if (
            not isinstance(file_path, str)
            or not file_path
            or "\x00" in file_path
            or "\\" in file_path
        ):
            raise InvalidDeclarationMetadataError(
                "Extraction results must use a non-empty relative POSIX file path."
            )
        windows_path = PureWindowsPath(file_path)
        posix_path = PurePosixPath(file_path)
        if (
            posix_path.is_absolute()
            or windows_path.is_absolute()
            or windows_path.drive
            or any(part in {"", ".", ".."} for part in file_path.split("/"))
        ):
            raise InvalidDeclarationMetadataError(
                "Extraction results must use a project-relative file path."
            )

    @classmethod
    def _validate_declaration_file_path(
        cls,
        declaration_file_path: object,
        result_file_path: str,
    ) -> None:
        cls._validate_relative_file_path(declaration_file_path)
        if declaration_file_path != result_file_path:
            raise InvalidDeclarationMetadataError(
                "A declaration file path does not match its extraction result."
            )

    @classmethod
    def _function_scope(
        cls,
        declaration: FunctionDeclaration,
    ) -> tuple[str, ...]:
        cls._validate_declaration_name(declaration.name)
        components = cls._normalize_scope_path(
            declaration.scope_path,
            declaration.name,
        )
        if not isinstance(declaration.declaration_type, DeclarationType):
            raise InvalidDeclarationMetadataError(
                "A function declaration has an invalid declaration type."
            )
        if declaration.declaration_type is DeclarationType.METHOD:
            if declaration.enclosing_class_name is None:
                raise InvalidDeclarationMetadataError(
                    "A method declaration is missing its enclosing class."
                )
        cls._validate_enclosing_name(
            declaration.enclosing_class_name,
            components[:-1],
            "enclosing class",
        )
        if (
            declaration.declaration_type is DeclarationType.FUNCTION
            and len(components) != 1
        ):
            raise InvalidDeclarationMetadataError(
                "A module-level function has an unexpected lexical scope path."
            )
        if declaration.declaration_type is DeclarationType.METHOD and (
            len(components) < 2
            or components[-2] != declaration.enclosing_class_name
        ):
            raise InvalidDeclarationMetadataError(
                "A method's immediate parent scope must be its enclosing class."
            )
        if declaration.declaration_type is DeclarationType.NESTED_FUNCTION and (
            len(components) < 2
        ):
            raise InvalidDeclarationMetadataError(
                "A nested function must be contained by its enclosing function."
            )
        cls._validate_source_location(
            declaration.start_line,
            declaration.definition_line,
            declaration.end_line,
            declaration.start_column,
            declaration.end_column,
        )
        return components

    @classmethod
    def _class_scope(
        cls,
        declaration: ClassDeclaration,
    ) -> tuple[str, ...]:
        cls._validate_declaration_name(declaration.name)
        components = cls._normalize_scope_path(
            declaration.scope_path,
            declaration.name,
        )
        parent_components = cls._normalize_parent_scope(
            declaration.parent_scope_path
        )
        if parent_components != components[:-1]:
            raise InvalidDeclarationMetadataError(
                "A class declaration has inconsistent parent and lexical scopes."
            )
        expected_parent_name = parent_components[-1] if parent_components else None
        if declaration.parent_scope_name != expected_parent_name:
            raise InvalidDeclarationMetadataError(
                "A class declaration has an inconsistent parent scope name."
            )
        if not isinstance(declaration.declaration_type, ClassDeclarationType):
            raise InvalidDeclarationMetadataError(
                "A class declaration has an invalid declaration type."
            )
        if not isinstance(declaration.parent_scope_type, ClassParentScopeType):
            raise InvalidDeclarationMetadataError(
                "A class declaration has an invalid parent scope type."
            )
        if declaration.declaration_type is ClassDeclarationType.TOP_LEVEL:
            if (
                parent_components
                or declaration.parent_scope_type is not ClassParentScopeType.MODULE
            ):
                raise InvalidDeclarationMetadataError(
                    "A top-level class has inconsistent parent scope metadata."
                )
        elif (
            not parent_components
            or declaration.parent_scope_type is ClassParentScopeType.MODULE
        ):
            raise InvalidDeclarationMetadataError(
                "A nested class has inconsistent parent scope metadata."
            )
        cls._validate_enclosing_name(
            declaration.enclosing_class_name,
            parent_components,
            "enclosing class",
        )
        cls._validate_enclosing_name(
            declaration.enclosing_function_name,
            parent_components,
            "enclosing function",
        )
        if declaration.declaration_type is ClassDeclarationType.NESTED:
            if declaration.parent_scope_type is ClassParentScopeType.CLASS:
                if declaration.enclosing_class_name != parent_components[-1]:
                    raise InvalidDeclarationMetadataError(
                        "A nested class's parent class metadata is inconsistent."
                    )
            elif declaration.parent_scope_type in {
                ClassParentScopeType.FUNCTION,
                ClassParentScopeType.METHOD,
            }:
                if declaration.enclosing_function_name != parent_components[-1]:
                    raise InvalidDeclarationMetadataError(
                        "A function-local class's enclosing function is inconsistent."
                    )
                if (
                    declaration.parent_scope_type is ClassParentScopeType.METHOD
                    and declaration.enclosing_class_name is None
                ):
                    raise InvalidDeclarationMetadataError(
                        "A class nested in a method is missing its enclosing class."
                    )
        cls._validate_source_location(
            declaration.start_line,
            declaration.definition_line,
            declaration.end_line,
            declaration.start_column,
            declaration.end_column,
        )
        return components

    @staticmethod
    def _normalize_scope_path(
        scope_path: object,
        declaration_name: str,
    ) -> tuple[str, ...]:
        if not isinstance(scope_path, (tuple, list)) or not scope_path:
            raise InvalidDeclarationMetadataError(
                "A declaration is missing its lexical scope path."
            )
        if any(
            not isinstance(component, str)
            or not component
            or "." in component
            for component in scope_path
        ):
            raise InvalidDeclarationMetadataError(
                "A declaration contains invalid lexical scope components."
            )
        components = tuple(scope_path)
        if components[-1] != declaration_name:
            raise InvalidDeclarationMetadataError(
                "The last lexical scope component must match the declaration name."
            )
        return components

    @staticmethod
    def _normalize_parent_scope(scope_path: object) -> tuple[str, ...]:
        if not isinstance(scope_path, (tuple, list)):
            raise InvalidDeclarationMetadataError(
                "A class declaration is missing its parent lexical scope."
            )
        if any(
            not isinstance(component, str)
            or not component
            or "." in component
            for component in scope_path
        ):
            raise InvalidDeclarationMetadataError(
                "A class declaration contains invalid parent scope components."
            )
        return tuple(scope_path)

    @staticmethod
    def _validate_declaration_name(name: object) -> None:
        if not isinstance(name, str) or not name or "." in name:
            raise InvalidDeclarationMetadataError(
                "A declaration must have a non-empty, undotted name."
            )

    @staticmethod
    def _validate_enclosing_name(
        enclosing_name: str | None,
        parent_components: tuple[str, ...],
        label: str,
    ) -> None:
        if enclosing_name is not None and (
            not isinstance(enclosing_name, str)
            or enclosing_name not in parent_components
        ):
            raise InvalidDeclarationMetadataError(
                f"A declaration has inconsistent {label} metadata."
            )

    @staticmethod
    def _validate_source_location(
        start_line: object,
        definition_line: object,
        end_line: object,
        start_column: object,
        end_column: object,
    ) -> None:
        if (
            not isinstance(start_line, int)
            or isinstance(start_line, bool)
            or start_line < 1
            or not isinstance(definition_line, int)
            or isinstance(definition_line, bool)
            or definition_line < 1
            or (end_line is not None and not isinstance(end_line, int))
            or (isinstance(end_line, bool))
            or (start_column is not None and not isinstance(start_column, int))
            or (end_column is not None and not isinstance(end_column, int))
            or (isinstance(start_column, bool))
            or (isinstance(end_column, bool))
            or (start_column is not None and start_column < 0)
            or (end_column is not None and end_column < 0)
            or (end_line is not None and end_line < definition_line)
            or start_line > definition_line
        ):
            raise InvalidDeclarationMetadataError(
                "A declaration contains invalid source location metadata."
            )

    @staticmethod
    def _source_position(
        declaration: QualifiedDeclaration,
    ) -> tuple[int, int, int]:
        return (
            declaration.start_line,
            declaration.start_column or 0,
            declaration.definition_line,
        )

    @staticmethod
    def _qualified_function(
        *,
        file_path: str,
        declaration: FunctionDeclaration,
        scope_components: tuple[str, ...],
    ) -> QualifiedDeclaration:
        declaration_type = {
            DeclarationType.FUNCTION: QualifiedDeclarationType.FUNCTION,
            DeclarationType.METHOD: QualifiedDeclarationType.METHOD,
            DeclarationType.NESTED_FUNCTION: QualifiedDeclarationType.NESTED_FUNCTION,
        }[declaration.declaration_type]
        parent_components = scope_components[:-1]
        return QualifiedDeclaration(
            file_path=file_path,
            original_name=declaration.name,
            qualified_name=".".join(scope_components),
            declaration_type=declaration_type,
            enclosing_scope=".".join(parent_components) or None,
            lexical_scope_components=scope_components,
            extraction_source_type=ExtractionSourceType.FUNCTION_EXTRACTOR,
            start_line=declaration.start_line,
            definition_line=declaration.definition_line,
            end_line=declaration.end_line,
            start_column=declaration.start_column,
            end_column=declaration.end_column,
            source_order=0,
        )

    @staticmethod
    def _qualified_class(
        *,
        file_path: str,
        declaration: ClassDeclaration,
        scope_components: tuple[str, ...],
    ) -> QualifiedDeclaration:
        parent_components = scope_components[:-1]
        return QualifiedDeclaration(
            file_path=file_path,
            original_name=declaration.name,
            qualified_name=".".join(scope_components),
            declaration_type=QualifiedDeclarationType.CLASS,
            enclosing_scope=".".join(parent_components) or None,
            lexical_scope_components=scope_components,
            extraction_source_type=ExtractionSourceType.CLASS_EXTRACTOR,
            start_line=declaration.start_line,
            definition_line=declaration.definition_line,
            end_line=declaration.end_line,
            start_column=declaration.start_column,
            end_column=declaration.end_column,
            source_order=0,
        )
