from enum import StrEnum


class TransactionCapability(StrEnum):
    NONE = "NONE"
    SINGLE_COLLECTION = "SINGLE_COLLECTION"
    MULTI_COLLECTION = "MULTI_COLLECTION"
    DISTRIBUTED = "DISTRIBUTED"


class CompensationCapability(StrEnum):
    NONE = "NONE"
    DELETE = "DELETE"
    DOMAIN_COMPENSATE = "DOMAIN_COMPENSATE"
