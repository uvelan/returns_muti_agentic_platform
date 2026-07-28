import re
from collections.abc import Callable
from typing import Any, Protocol

from return_platform.data_platform.schema_registry import (
    DataAssetSchema,
    SchemaField,
    SchemaRegistry,
)

from .models import (
    FindingCode,
    GuardFinding,
    GuardSeverity,
    OperationProposal,
    ValidationResult,
    ValidationResultState,
)


class ExistenceResolver(Protocol):
    def exists(self, field_path: str, value: Any) -> bool: ...


def validate_proposal(
    registry: SchemaRegistry,
    proposal: OperationProposal,
    resolver: ExistenceResolver | None = None,
    tenant_id: str | None = None,
    pii_validator: Callable[[str, Any], bool] | None = None,
) -> ValidationResult:
    findings: list[GuardFinding] = []

    try:
        asset = registry.asset(proposal.asset_id)
    except KeyError:
        findings.append(
            GuardFinding(
                code=FindingCode.MISSING_ASSET,
                severity=GuardSeverity.DENIAL,
                asset_id=proposal.asset_id,
                message=f"Asset {proposal.asset_id} is not defined in the schema registry",
            )
        )
        return ValidationResult(state=ValidationResultState.INVALID_PROPOSAL, findings=findings)

    if asset.owner == "OMC":
        findings.append(
            GuardFinding(
                code=FindingCode.OMC_DENIAL,
                severity=GuardSeverity.DENIAL,
                asset_id=asset.asset_id,
                message="OMC assets cannot be modified by operational generation",
            )
        )

    if asset.write_policy == "DENIED":
        findings.append(
            GuardFinding(
                code=FindingCode.POLICY_DENIAL,
                severity=GuardSeverity.DENIAL,
                asset_id=asset.asset_id,
                message="Asset policy denies operational generation",
            )
        )

    if asset.ownership == "DERIVED_PROJECTION" or asset.write_policy == "DERIVED_PROJECTION":
        findings.append(
            GuardFinding(
                code=FindingCode.DERIVED_PROJECTION_DENIAL,
                severity=GuardSeverity.DENIAL,
                asset_id=asset.asset_id,
                message="Derived projections cannot be modified directly",
            )
        )

    if asset.generated_data_policy != "ENABLED":
        findings.append(
            GuardFinding(
                code=FindingCode.GENERATED_DATA_DISABLED,
                severity=GuardSeverity.DENIAL,
                asset_id=asset.asset_id,
                message="Generated data is disabled for this asset",
            )
        )

    field_map = {f.name: f for f in asset.fields}
    natural_keys_seen = set()

    for idx, record in enumerate(proposal.records):
        if not isinstance(record, dict):
            findings.append(
                GuardFinding(
                    code=FindingCode.INVALID_JSON_SHAPE,
                    severity=GuardSeverity.ERROR,
                    asset_id=asset.asset_id,
                    record_index=idx,
                    message="Record must be a JSON object",
                )
            )
            continue

        for key, value in record.items():
            if key not in field_map:
                findings.append(
                    GuardFinding(
                        code=FindingCode.UNKNOWN_FIELD,
                        severity=GuardSeverity.ERROR,
                        asset_id=asset.asset_id,
                        record_index=idx,
                        field_path=key,
                        message=f"Unknown field '{key}'",
                        rejected_value=str(value)[:100],
                    )
                )
            else:
                field_schema = field_map[key]
                validate_field(findings, asset, idx, key, value, field_schema)

                if key in asset.dependency_fields:
                    if resolver and not resolver.exists(key, value):
                        findings.append(
                            GuardFinding(
                                code=FindingCode.INVALID_FOREIGN_KEY,
                                severity=GuardSeverity.ERROR,
                                asset_id=asset.asset_id,
                                record_index=idx,
                                field_path=key,
                                message=f"Foreign key '{key}' value '{value}' does not exist",
                                rejected_value=str(value)[:100],
                            )
                        )

                if asset.pii_policy == "STRICT_SYNTHETIC" and pii_validator:
                    if not pii_validator(key, value):
                        findings.append(
                            GuardFinding(
                                code=FindingCode.PII_VIOLATION,
                                severity=GuardSeverity.ERROR,
                                asset_id=asset.asset_id,
                                record_index=idx,
                                field_path=key,
                                message="Value violates STRICT_SYNTHETIC PII policy",
                                rejected_value=str(value)[:100],
                            )
                        )

                if tenant_id and key == "tenant_id" and value != tenant_id:
                    findings.append(
                        GuardFinding(
                            code=FindingCode.POLICY_DENIAL,
                            severity=GuardSeverity.ERROR,
                            asset_id=asset.asset_id,
                            record_index=idx,
                            field_path=key,
                            message=f"Tenant scope violation: expected {tenant_id}",
                            rejected_value=str(value)[:100],
                        )
                    )

        for field in asset.fields:
            if field.required and field.name not in record:
                findings.append(
                    GuardFinding(
                        code=FindingCode.MISSING_REQUIRED_FIELD,
                        severity=GuardSeverity.ERROR,
                        asset_id=asset.asset_id,
                        record_index=idx,
                        field_path=field.name,
                        message=f"Missing required field '{field.name}'",
                    )
                )

        if asset.natural_keys:
            key_tuple = tuple(record.get(k) for k in asset.natural_keys)
            if all(k is not None for k in key_tuple):
                if key_tuple in natural_keys_seen:
                    findings.append(
                        GuardFinding(
                            code=FindingCode.DUPLICATE_NATURAL_KEY,
                            severity=GuardSeverity.ERROR,
                            asset_id=asset.asset_id,
                            record_index=idx,
                            message=f"Duplicate natural key {key_tuple}",
                        )
                    )
                else:
                    natural_keys_seen.add(key_tuple)

    # Deterministic finding order
    findings.sort(key=lambda f: (f.record_index or -1, f.field_path or "", f.code.value))

    if any(f.severity == GuardSeverity.DENIAL for f in findings):
        state = ValidationResultState.POLICY_DENIED
    elif any(
        f.code
        in (
            FindingCode.UNKNOWN_FIELD,
            FindingCode.INVALID_JSON_SHAPE,
            FindingCode.DUPLICATE_NATURAL_KEY,
            FindingCode.MISSING_REQUIRED_FIELD,
            FindingCode.INVALID_TYPE,
            FindingCode.INVALID_ENUM,
            FindingCode.PATTERN_MISMATCH,
            FindingCode.LENGTH_VIOLATION,
            FindingCode.RANGE_VIOLATION,
            FindingCode.INVALID_FOREIGN_KEY,
            FindingCode.PII_VIOLATION,
        )
        for f in findings
    ):
        state = ValidationResultState.INVALID_RECORD
    else:
        state = ValidationResultState.VALID

    return ValidationResult(state=state, findings=findings)


def validate_field(
    findings: list[GuardFinding],
    asset: DataAssetSchema,
    idx: int,
    key: str,
    value: Any,
    schema: SchemaField,
) -> None:
    if value is None:
        if schema.required:
            findings.append(
                GuardFinding(
                    code=FindingCode.MISSING_REQUIRED_FIELD,
                    severity=GuardSeverity.ERROR,
                    asset_id=asset.asset_id,
                    record_index=idx,
                    field_path=key,
                    message=f"Field '{key}' is required but null",
                )
            )
        return

    type_map: dict[str, Any] = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "object": dict,
        "array": list,
    }

    expected_type = type_map.get(schema.type)
    if expected_type and not isinstance(value, expected_type):
        findings.append(
            GuardFinding(
                code=FindingCode.INVALID_TYPE,
                severity=GuardSeverity.ERROR,
                asset_id=asset.asset_id,
                record_index=idx,
                field_path=key,
                message=f"Expected {schema.type}, got {type(value).__name__}",
                expected_value=schema.type,
                rejected_value=str(value)[:100],
            )
        )
        return

    if isinstance(value, str):
        if schema.min_length is not None and len(value) < schema.min_length:
            findings.append(
                GuardFinding(
                    code=FindingCode.LENGTH_VIOLATION,
                    severity=GuardSeverity.ERROR,
                    asset_id=asset.asset_id,
                    record_index=idx,
                    field_path=key,
                    message=f"String length {len(value)} is less than minimum {schema.min_length}",
                )
            )
        if schema.max_length is not None and len(value) > schema.max_length:
            findings.append(
                GuardFinding(
                    code=FindingCode.LENGTH_VIOLATION,
                    severity=GuardSeverity.ERROR,
                    asset_id=asset.asset_id,
                    record_index=idx,
                    field_path=key,
                    message=f"String length {len(value)} is greater than maximum {schema.max_length}",
                )
            )
        if schema.pattern:
            if not re.match(schema.pattern, value):
                findings.append(
                    GuardFinding(
                        code=FindingCode.PATTERN_MISMATCH,
                        severity=GuardSeverity.ERROR,
                        asset_id=asset.asset_id,
                        record_index=idx,
                        field_path=key,
                        message=f"String does not match pattern {schema.pattern}",
                        rejected_value=value[:100],
                    )
                )

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if schema.minimum is not None and value < schema.minimum:
            findings.append(
                GuardFinding(
                    code=FindingCode.RANGE_VIOLATION,
                    severity=GuardSeverity.ERROR,
                    asset_id=asset.asset_id,
                    record_index=idx,
                    field_path=key,
                    message=f"Value {value} is less than minimum {schema.minimum}",
                )
            )
        if schema.maximum is not None and value > schema.maximum:
            findings.append(
                GuardFinding(
                    code=FindingCode.RANGE_VIOLATION,
                    severity=GuardSeverity.ERROR,
                    asset_id=asset.asset_id,
                    record_index=idx,
                    field_path=key,
                    message=f"Value {value} is greater than maximum {schema.maximum}",
                )
            )

    if schema.enum is not None and value not in schema.enum:
        findings.append(
            GuardFinding(
                code=FindingCode.INVALID_ENUM,
                severity=GuardSeverity.ERROR,
                asset_id=asset.asset_id,
                record_index=idx,
                field_path=key,
                message=f"Value not in enum: {schema.enum}",
                rejected_value=str(value)[:100],
            )
        )
