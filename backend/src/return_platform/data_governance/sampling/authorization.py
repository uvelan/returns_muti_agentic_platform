"""Catalog-backed authorization for bounded data sampling."""

from enum import StrEnum

from return_platform.data_governance.sampling.contracts import (
    MAX_SAMPLE_ROWS,
)
from return_platform.shared.governance import (
    AllowedOperation,
    AssetCatalog,
    AssetCatalogEntry,
    DataStoreType,
    ObjectKind,
)


class SamplingAuthorizationCode(StrEnum):
    """Stable reason for rejecting a sampling request."""

    ASSET_NOT_FOUND = "ASSET_NOT_FOUND"
    STORE_MISMATCH = "STORE_MISMATCH"
    READ_NOT_ALLOWED = "READ_NOT_ALLOWED"
    SAMPLING_DISABLED = "SAMPLING_DISABLED"
    INVALID_ROW_LIMIT = "INVALID_ROW_LIMIT"
    UNSUPPORTED_OBJECT_KIND = "UNSUPPORTED_OBJECT_KIND"
    INVALID_REDACTION_CONFIGURATION = "INVALID_REDACTION_CONFIGURATION"


class SamplingAuthorizationError(PermissionError):
    """Safe rejection produced by the sampling authorization boundary."""

    code: SamplingAuthorizationCode

    def __init__(
        self,
        *,
        code: SamplingAuthorizationCode,
        message: str,
    ) -> None:
        super().__init__(message)
        self.code = code


def _find_catalog_asset(
    *,
    catalog: AssetCatalog,
    asset_id: str,
) -> AssetCatalogEntry:
    """Resolve an asset exclusively from the approved catalog."""

    if not asset_id:
        raise ValueError(
            "Sampling asset ID must not be empty.",
        )

    if asset_id != asset_id.strip():
        raise ValueError(
            "Sampling asset ID must not contain surrounding whitespace.",
        )

    matches = tuple(asset for asset in catalog.assets if asset.asset_id == asset_id)

    if not matches:
        raise SamplingAuthorizationError(
            code=SamplingAuthorizationCode.ASSET_NOT_FOUND,
            message="Sampling asset is not present in the catalog.",
        )

    if len(matches) != 1:
        raise SamplingAuthorizationError(
            code=SamplingAuthorizationCode.ASSET_NOT_FOUND,
            message="Sampling asset identity is ambiguous.",
        )

    return matches[0]


def _validate_store(
    *,
    asset: AssetCatalogEntry,
    expected_store: DataStoreType,
) -> None:
    """Ensure the requested engine matches the catalog asset."""

    if asset.store != expected_store:
        raise SamplingAuthorizationError(
            code=SamplingAuthorizationCode.STORE_MISMATCH,
            message="Sampling asset does not belong to the requested store.",
        )


def _validate_object_kind(
    asset: AssetCatalogEntry,
) -> None:
    """Ensure the physical object kind is supported by its store."""

    if asset.store == DataStoreType.SQLSERVER:
        if asset.object_kind not in {
            ObjectKind.TABLE,
            ObjectKind.VIEW,
        }:
            raise SamplingAuthorizationError(
                code=(SamplingAuthorizationCode.UNSUPPORTED_OBJECT_KIND),
                message=("SQL Server sampling supports only cataloged tables and views."),
            )

        if asset.namespace is None:
            raise SamplingAuthorizationError(
                code=(SamplingAuthorizationCode.UNSUPPORTED_OBJECT_KIND),
                message=("SQL Server sampling requires a cataloged namespace."),
            )

        return

    if asset.store == DataStoreType.MONGODB:
        if asset.object_kind != ObjectKind.COLLECTION:
            raise SamplingAuthorizationError(
                code=(SamplingAuthorizationCode.UNSUPPORTED_OBJECT_KIND),
                message=("MongoDB sampling supports only cataloged collections."),
            )

        if asset.namespace is not None:
            raise SamplingAuthorizationError(
                code=(SamplingAuthorizationCode.UNSUPPORTED_OBJECT_KIND),
                message=("MongoDB sampling assets must not use a namespace."),
            )

        return

    raise SamplingAuthorizationError(
        code=SamplingAuthorizationCode.UNSUPPORTED_OBJECT_KIND,
        message="Sampling is unsupported for this data store.",
    )


def _validate_read_permission(
    asset: AssetCatalogEntry,
) -> None:
    """Require explicit read permission in the approved catalog."""

    if AllowedOperation.READ not in asset.allowed_operations:
        raise SamplingAuthorizationError(
            code=SamplingAuthorizationCode.READ_NOT_ALLOWED,
            message="Catalog policy does not allow reading this asset.",
        )


def _validate_sampling_configuration(
    asset: AssetCatalogEntry,
) -> None:
    """Validate the catalog's explicit sampling authorization."""

    sampling = asset.sampling

    if not sampling.enabled:
        raise SamplingAuthorizationError(
            code=SamplingAuthorizationCode.SAMPLING_DISABLED,
            message="Sampling is disabled for this catalog asset.",
        )

    if not 1 <= sampling.max_rows <= MAX_SAMPLE_ROWS:
        raise SamplingAuthorizationError(
            code=SamplingAuthorizationCode.INVALID_ROW_LIMIT,
            message="Catalog sampling row limit is outside the safe bound.",
        )

    normalized_redaction_fields = tuple(
        field_name.casefold() for field_name in sampling.redact_fields
    )

    if len(set(normalized_redaction_fields)) != len(
        normalized_redaction_fields,
    ):
        raise SamplingAuthorizationError(
            code=(SamplingAuthorizationCode.INVALID_REDACTION_CONFIGURATION),
            message=("Catalog redaction fields contain case-insensitive duplicates."),
        )


def authorize_sampling_asset(
    *,
    catalog: AssetCatalog,
    asset_id: str,
    expected_store: DataStoreType,
) -> AssetCatalogEntry:
    """
    Resolve and authorize one sampling asset from the approved catalog.

    The caller supplies only an asset ID. The returned entry is always the
    immutable entry stored in the supplied catalog; caller-created asset
    entries are never accepted as authorization evidence.
    """

    asset = _find_catalog_asset(
        catalog=catalog,
        asset_id=asset_id,
    )

    _validate_store(
        asset=asset,
        expected_store=expected_store,
    )
    _validate_object_kind(
        asset,
    )
    _validate_read_permission(
        asset,
    )
    _validate_sampling_configuration(
        asset,
    )

    return asset
