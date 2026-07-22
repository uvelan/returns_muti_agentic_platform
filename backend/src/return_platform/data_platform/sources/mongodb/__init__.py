"""MongoDB source adapters."""

from return_platform.data_platform.sources.mongodb.customer import (
    CUSTOMER_FIND_COMMENT,
    MAX_CUSTOMER_SOURCE_TIMEOUT_SECONDS,
    MIN_CUSTOMER_SOURCE_TIMEOUT_SECONDS,
    CustomerMongoSourceAdapter,
    CustomerMongoSourceError,
    CustomerMongoSourceErrorCode,
    FetchedCustomerSourceDocument,
)

__all__ = [
    "CUSTOMER_FIND_COMMENT",
    "MAX_CUSTOMER_SOURCE_TIMEOUT_SECONDS",
    "MIN_CUSTOMER_SOURCE_TIMEOUT_SECONDS",
    "CustomerMongoSourceAdapter",
    "CustomerMongoSourceError",
    "CustomerMongoSourceErrorCode",
    "FetchedCustomerSourceDocument",
]
