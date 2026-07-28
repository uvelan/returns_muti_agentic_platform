"""Validate bootstrap AI settings and convert them to graph-owned Vault bindings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pymongo import AsyncMongoClient

from return_platform.ai_gateway.configuration import LoadedAIGatewayConfiguration
from return_platform.configuration.return_configuration import (
    AIModelBindingConfiguration,
    AIProviderRuntimeConfiguration,
    AIValidatedRouteConfiguration,
    CredentialBindingConfiguration,
    ReturnPlatformConfiguration,
    RuntimeIntegrationsConfiguration,
)
from return_platform.configuration.settings import Settings
from return_platform.data_console.api.runtime_validation import (
    ai_binding_checksum,
    probe_ai_provider_model,
)
from return_platform.secrets.vault import ResolvedSecret, SecretResolver, parse_secret_reference
from return_platform.validation.gates import SecretValidationGate, ValidationReceipt

_RECEIPT_COLLECTION = "configuration_validation_receipts"
_HOSTED_PROVIDERS = ("GOOGLE", "NVIDIA", "OPENAI", "ANTHROPIC")


@dataclass(frozen=True, slots=True)
class _ValidatedPair:
    credential_index: int
    model_id: str
    model_class: str
    tests: tuple[str, ...]


def _provider_base_url(provider: str, settings: Settings) -> str:
    return str(getattr(settings, f"{provider.lower()}_base_url"))


def _provider_models(settings: Settings, provider: str) -> tuple[tuple[str, str], ...]:
    key = provider.lower()
    lightweight = tuple(getattr(settings, f"{key}_lightweight_models"))
    standard = tuple(getattr(settings, f"{key}_standard_models"))
    return tuple((model, "LIGHTWEIGHT") for model in lightweight) + tuple(
        (model, "STANDARD") for model in standard
    )


def _provider_credential_count(settings: Settings, provider: str) -> int:
    return len(tuple(getattr(settings, f"resolved_{provider.lower()}_api_keys")))


def _task_keys(
    loaded: LoadedAIGatewayConfiguration,
    provider: str,
    model_class: str,
) -> tuple[str, ...]:
    return tuple(
        task_key
        for task_key, task in loaded.configuration.tasks.items()
        if task.tier.value == model_class and provider in task.allowedProviders
    )


async def _persist_or_reuse_receipt(
    collection: Any,
    receipt: ValidationReceipt,
) -> ValidationReceipt:
    lookup = {
        "status": "PASSED",
        "target_uri": receipt.target_uri,
        "subject_type": receipt.subject_type,
        "subject_key": receipt.subject_key,
        "configuration_checksum": receipt.configuration_checksum,
        "secret_fingerprint": receipt.secret_fingerprint,
        "secret_version": receipt.secret_version,
    }
    existing = await collection.find_one(lookup, {"_id": 0})
    if existing is not None:
        existing["receipt_id"] = str(existing["receipt_id"])
        return ValidationReceipt.model_validate(existing)
    payload = receipt.model_dump(mode="python")
    payload["receipt_id"] = str(receipt.receipt_id)
    await collection.update_one(
        {"receipt_id": payload["receipt_id"]},
        {"$setOnInsert": payload},
        upsert=True,
    )
    return receipt


async def _cached_pair_tests(
    *,
    collection: Any,
    settings: Settings,
    resolved: ResolvedSecret,
    provider: str,
    model_id: str,
    model_class: str,
    task_key: str,
) -> tuple[str, ...] | None:
    checksum = ai_binding_checksum(
        provider=provider,
        base_url=_provider_base_url(provider, settings),
        model_id=model_id,
        model_class=model_class,
        task_key=task_key,
        vault_reference=resolved.reference.to_uri(),
    )
    existing = await collection.find_one(
        {
            "status": "PASSED",
            "target_uri": resolved.reference.to_uri(),
            "subject_type": "AI_CREDENTIAL_MODEL_BINDING",
            "subject_key": f"{provider}:{model_id}:{task_key}",
            "configuration_checksum": checksum,
            "secret_version": resolved.secret_version,
        },
        {"_id": 0},
    )
    if existing is None:
        return None
    existing["receipt_id"] = str(existing["receipt_id"])
    receipt = ValidationReceipt.model_validate(existing)
    expected = SecretValidationGate.receipt_for_validated_secret(
        resolved=resolved,
        tests=receipt.tests,
        subject_type=receipt.subject_type,
        subject_key=receipt.subject_key,
        configuration_checksum=checksum,
        fingerprint_key=settings.validation_fingerprint_key.get_secret_value().encode("utf-8"),
        actor_id="runtime-bootstrap",
    )
    if expected.secret_fingerprint != receipt.secret_fingerprint:
        return None
    return receipt.tests


async def build_bootstrap_runtime_configuration(
    *,
    settings: Settings,
    resolver: SecretResolver,
    loaded_ai_gateway: LoadedAIGatewayConfiguration,
    configuration: ReturnPlatformConfiguration,
) -> ReturnPlatformConfiguration:
    """Return a configuration containing only provider/model pairs that pass live probes."""

    mongo: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        settings.mongo_dsn.get_secret_value()
    )
    collection = mongo[settings.mongo_database][_RECEIPT_COLLECTION]
    await collection.create_index("receipt_id", unique=True)
    await collection.create_index(
        [
            ("subject_key", 1),
            ("configuration_checksum", 1),
            ("secret_fingerprint", 1),
            ("secret_version", 1),
        ]
    )
    providers: list[AIProviderRuntimeConfiguration] = []
    configured_order = tuple(item.strip() for item in settings.ai_provider_order.split(","))
    try:
        for provider_priority, provider in enumerate(configured_order, start=1):
            if provider not in _HOSTED_PROVIDERS:
                continue
            credential_count = _provider_credential_count(settings, provider)
            models = _provider_models(settings, provider)
            if credential_count == 0 or not models:
                continue

            resolved_credentials = []
            for index in range(1, credential_count + 1):
                reference = parse_secret_reference(
                    "vault://secret/production/"
                    f"ai/{provider.lower()}/credentials/key-{index}#api_key"
                )
                resolved = await resolver.get_secret(reference)
                if resolved is not None:
                    resolved_credentials.append((index, resolved))

            validated_pairs: list[_ValidatedPair] = []
            for credential_index, resolved in resolved_credentials:
                for model_id, model_class in models:
                    task_keys = _task_keys(loaded_ai_gateway, provider, model_class)
                    cached_tests = (
                        await _cached_pair_tests(
                            collection=collection,
                            settings=settings,
                            resolved=resolved,
                            provider=provider,
                            model_id=model_id,
                            model_class=model_class,
                            task_key=task_keys[0],
                        )
                        if task_keys
                        else None
                    )
                    try:
                        tests = (
                            cached_tests
                            if cached_tests is not None
                            else await probe_ai_provider_model(
                                provider=provider,
                                base_url=_provider_base_url(provider, settings),
                                api_key=resolved.value,
                                model_id=model_id,
                                settings=settings,
                            )
                        )
                    except Exception as exc:
                        print(
                            "ai_bootstrap_validation="
                            f"FAILED provider={provider} model={model_id} "
                            f"error={type(exc).__name__}"
                        )
                        continue
                    validated_pairs.append(
                        _ValidatedPair(
                            credential_index=credential_index,
                            model_id=model_id,
                            model_class=model_class,
                            tests=tests,
                        )
                    )

            active_credential_indexes = {
                index
                for index, _resolved in resolved_credentials
                if any(pair.credential_index == index for pair in validated_pairs)
            }
            active_models = {
                (model_id, model_class)
                for model_id, model_class in models
                if active_credential_indexes
                and all(
                    any(
                        pair.credential_index == index and pair.model_id == model_id
                        for pair in validated_pairs
                    )
                    for index in active_credential_indexes
                )
            }
            active_credential_indexes = {
                index
                for index in active_credential_indexes
                if all(
                    any(
                        pair.credential_index == index and pair.model_id == model_id
                        for pair in validated_pairs
                    )
                    for model_id, _model_class in active_models
                )
            }
            if not active_credential_indexes or not active_models:
                continue

            route_bindings: list[AIValidatedRouteConfiguration] = []
            credential_receipts: dict[int, tuple[str, str]] = {}
            for pair in validated_pairs:
                if (
                    pair.credential_index not in active_credential_indexes
                    or (pair.model_id, pair.model_class) not in active_models
                ):
                    continue
                resolved = next(
                    item for index, item in resolved_credentials if index == pair.credential_index
                )
                for task_key in _task_keys(
                    loaded_ai_gateway,
                    provider,
                    pair.model_class,
                ):
                    checksum = ai_binding_checksum(
                        provider=provider,
                        base_url=_provider_base_url(provider, settings),
                        model_id=pair.model_id,
                        model_class=pair.model_class,
                        task_key=task_key,
                        vault_reference=resolved.reference.to_uri(),
                    )
                    receipt = SecretValidationGate.receipt_for_validated_secret(
                        resolved=resolved,
                        tests=pair.tests,
                        subject_type="AI_CREDENTIAL_MODEL_BINDING",
                        subject_key=f"{provider}:{pair.model_id}:{task_key}",
                        configuration_checksum=checksum,
                        fingerprint_key=(
                            settings.validation_fingerprint_key.get_secret_value().encode("utf-8")
                        ),
                        actor_id="runtime-bootstrap",
                    )
                    receipt = await _persist_or_reuse_receipt(collection, receipt)
                    receipt_id = str(receipt.receipt_id)
                    credential_receipts.setdefault(
                        pair.credential_index,
                        (receipt_id, checksum),
                    )
                    route_bindings.append(
                        AIValidatedRouteConfiguration(
                            credential_profile_key=f"{provider.lower()}-key-{pair.credential_index}",
                            model_id=pair.model_id,
                            task_key=task_key,
                            validation_receipt_id=receipt_id,
                            validation_configuration_checksum=checksum,
                        )
                    )

            credentials = tuple(
                CredentialBindingConfiguration(
                    profile_key=f"{provider.lower()}-key-{index}",
                    vault_reference=resolved.reference.to_uri(),
                    validation_receipt_id=credential_receipts[index][0],
                    validation_configuration_checksum=credential_receipts[index][1],
                )
                for index, resolved in resolved_credentials
                if index in active_credential_indexes
            )
            model_bindings = tuple(
                AIModelBindingConfiguration(
                    model_id=model_id,
                    model_class=model_class,
                    task_keys=_task_keys(loaded_ai_gateway, provider, model_class),
                    priority=priority,
                )
                for priority, (model_id, model_class) in enumerate(models, start=1)
                if (model_id, model_class) in active_models
            )
            providers.append(
                AIProviderRuntimeConfiguration(
                    provider_key=provider,
                    enabled=True,
                    base_url=_provider_base_url(provider, settings),
                    credentials=credentials,
                    models=model_bindings,
                    validated_routes=tuple(route_bindings),
                    priority=provider_priority,
                )
            )
            print(
                "ai_bootstrap_validation="
                f"PASSED provider={provider} credentials={len(credentials)} "
                f"models={len(model_bindings)}"
            )
    finally:
        await mongo.close()

    integrations = RuntimeIntegrationsConfiguration(
        ai_providers=tuple(providers),
        data_sources=configuration.runtime_integrations.data_sources,
    )
    return configuration.model_copy(update={"runtime_integrations": integrations})
