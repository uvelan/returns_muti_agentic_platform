"""Prepare configured AI routes and record pair-specific validation evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

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
from return_platform.configuration.runtime_validation import (
    ai_binding_checksum,
    probe_ai_provider_model,
)
from return_platform.configuration.settings import Settings
from return_platform.secrets.vault import (
    ResolvedSecret,
    SecretResolver,
    parse_secret_reference,
)
from return_platform.validation.gates import SecretValidationGate, ValidationReceipt

_RECEIPT_COLLECTION = "configuration_validation_receipts"
_VALIDATION_RUN_COLLECTION = "ai_validation_runs"
#: The hosted providers bootstrap will validate credentials for. Public
#: because `/api/runtime-config` advertises the same set to the shell, and
#: a second copy of the tuple over there could drift from the one that is
#: actually enforced below.
HOSTED_AI_PROVIDERS = ("GOOGLE", "NVIDIA", "OPENAI", "ANTHROPIC")
_MAX_BOOTSTRAP_CREDENTIALS_PER_PROVIDER = 100


@dataclass(frozen=True, slots=True)
class _ValidatedPair:
    credential_index: int
    model_id: str
    model_class: str
    tests: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AIValidationRunDecision:
    allowed: bool
    run_id: str | None
    reason: str


def _project_validated_pairs(
    validated_pairs: list[_ValidatedPair],
) -> tuple[set[int], set[tuple[str, str]]]:
    """Project successful pairs for reporting without restricting configured routes."""

    return (
        {pair.credential_index for pair in validated_pairs},
        {(pair.model_id, pair.model_class) for pair in validated_pairs},
    )


def _provider_base_url(provider: str, settings: Settings) -> str:
    return str(getattr(settings, f"{provider.lower()}_base_url"))


def _provider_models(
    settings: Settings,
    provider: str,
) -> tuple[tuple[str, str], ...]:
    key = provider.lower()
    lightweight = tuple(getattr(settings, f"{key}_lightweight_models"))
    standard = tuple(getattr(settings, f"{key}_standard_models"))
    return tuple((model, "LIGHTWEIGHT") for model in lightweight) + tuple(
        (model, "STANDARD") for model in standard
    )


async def _resolve_provider_credentials(
    *,
    settings: Settings,
    resolver: SecretResolver,
    provider: str,
) -> list[tuple[int, ResolvedSecret]]:
    """Resolve configured references or discover contiguous bootstrap Vault keys."""

    key = provider.lower()
    configured_references = tuple(getattr(settings, f"{key}_api_key_references"))
    if configured_references:
        resolved_credentials: list[tuple[int, ResolvedSecret]] = []
        for index, reference_text in enumerate(
            configured_references,
            start=1,
        ):
            resolved = await resolver.get_secret(parse_secret_reference(reference_text))
            if resolved is not None:
                resolved_credentials.append((index, resolved))
        return resolved_credentials

    resolved_credentials = []
    for index in range(1, _MAX_BOOTSTRAP_CREDENTIALS_PER_PROVIDER + 1):
        reference = parse_secret_reference(
            f"vault://secret/production/ai/{key}/credentials/key-{index}#api_key"
        )
        resolved = await resolver.get_secret(reference)
        if resolved is None:
            break
        resolved_credentials.append((index, resolved))
    return resolved_credentials


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


def _model_bindings(
    *,
    settings: Settings,
    provider: str,
    loaded_ai_gateway: LoadedAIGatewayConfiguration,
) -> tuple[AIModelBindingConfiguration, ...]:
    values: list[AIModelBindingConfiguration] = []
    for priority, (model_id, model_class) in enumerate(
        _provider_models(settings, provider),
        start=1,
    ):
        task_keys = _task_keys(
            loaded_ai_gateway,
            provider,
            model_class,
        )
        if not task_keys:
            continue
        values.append(
            AIModelBindingConfiguration(
                model_id=model_id,
                model_class=model_class,
                task_keys=task_keys,
                priority=priority,
            )
        )
    return tuple(values)


async def begin_ai_validation_run(
    *,
    settings: Settings,
    force: bool = False,
) -> AIValidationRunDecision:
    """Allow one completed live validation run per configured interval."""

    mongo: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        settings.mongo_dsn.get_secret_value(),
        tz_aware=True,
    )
    collection = mongo[settings.mongo_database][_VALIDATION_RUN_COLLECTION]
    now = datetime.now(UTC)
    interval = timedelta(hours=settings.ai_validation_interval_hours)
    try:
        await collection.create_index("run_id", unique=True)
        await collection.create_index([("status", 1), ("completed_at", -1)])
        if not force:
            recent = await collection.find_one(
                {
                    "status": "COMPLETED",
                    "completed_at": {"$gte": now - interval},
                },
                {"_id": 0, "run_id": 1, "completed_at": 1},
                sort=[("completed_at", -1)],
            )
            if recent is not None:
                return AIValidationRunDecision(
                    allowed=False,
                    run_id=str(recent.get("run_id", "")) or None,
                    reason="completed-within-validation-interval",
                )

            running = await collection.find_one(
                {
                    "status": "RUNNING",
                    "started_at": {"$gte": now - timedelta(hours=1)},
                },
                {"_id": 0, "run_id": 1},
                sort=[("started_at", -1)],
            )
            if running is not None:
                return AIValidationRunDecision(
                    allowed=False,
                    run_id=str(running.get("run_id", "")) or None,
                    reason="validation-already-running",
                )

        run_id = str(uuid4())
        await collection.insert_one(
            {
                "run_id": run_id,
                "status": "RUNNING",
                "started_at": now,
                "forced": force,
                "interval_hours": settings.ai_validation_interval_hours,
            }
        )
        return AIValidationRunDecision(
            allowed=True,
            run_id=run_id,
            reason="forced" if force else "validation-due",
        )
    finally:
        await mongo.close()


async def finish_ai_validation_run(
    *,
    settings: Settings,
    run_id: str,
    status: str,
    error_type: str | None = None,
) -> None:
    """Close a daily validation run without persisting exception details."""

    if status not in {"COMPLETED", "FAILED"}:
        raise ValueError("AI validation run status must be COMPLETED or FAILED")

    mongo: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        settings.mongo_dsn.get_secret_value(),
        tz_aware=True,
    )
    try:
        payload: dict[str, object] = {
            "status": status,
            "completed_at": datetime.now(UTC),
        }
        if error_type is not None:
            payload["error_type"] = error_type
        await mongo[settings.mongo_database][_VALIDATION_RUN_COLLECTION].update_one(
            {"run_id": run_id},
            {"$set": payload},
        )
    finally:
        await mongo.close()


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
        "valid_until": {"$gt": datetime.now(UTC)},
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
    now = datetime.now(UTC)
    existing = await collection.find_one(
        {
            "status": "PASSED",
            "target_uri": resolved.reference.to_uri(),
            "subject_type": "AI_CREDENTIAL_MODEL_BINDING",
            "subject_key": f"{provider}:{model_id}:{task_key}",
            "configuration_checksum": checksum,
            "secret_version": resolved.secret_version,
            "verified_at": {"$gte": now - timedelta(hours=settings.ai_validation_interval_hours)},
            "valid_until": {"$gt": now},
        },
        {"_id": 0},
        sort=[("verified_at", -1)],
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
        fingerprint_key=(settings.validation_fingerprint_key.get_secret_value().encode("utf-8")),
        actor_id="runtime-bootstrap",
    )
    if expected.secret_fingerprint != receipt.secret_fingerprint:
        return None
    return receipt.tests


def _retained_validated_routes(
    *,
    existing_provider: AIProviderRuntimeConfiguration | None,
    credentials: tuple[CredentialBindingConfiguration, ...],
    models: tuple[AIModelBindingConfiguration, ...],
) -> tuple[AIValidatedRouteConfiguration, ...]:
    if existing_provider is None:
        return ()

    known_credentials = {item.profile_key for item in credentials}
    known_model_tasks = {
        (model.model_id, task_key) for model in models for task_key in model.task_keys
    }
    return tuple(
        route
        for route in existing_provider.validated_routes
        if route.credential_profile_key in known_credentials
        and (route.model_id, route.task_key) in known_model_tasks
    )


async def build_configured_runtime_configuration(
    *,
    settings: Settings,
    resolver: SecretResolver,
    loaded_ai_gateway: LoadedAIGatewayConfiguration,
    configuration: ReturnPlatformConfiguration,
    existing_configuration: ReturnPlatformConfiguration | None = None,
) -> ReturnPlatformConfiguration:
    """Publish every configured route without making provider network calls."""

    existing_by_provider: dict[str, AIProviderRuntimeConfiguration] = {
        item.provider_key: item
        for item in (
            existing_configuration.runtime_integrations.ai_providers
            if existing_configuration is not None
            else ()
        )
    }
    providers: list[AIProviderRuntimeConfiguration] = []
    configured_order = tuple(item.strip() for item in settings.ai_provider_order.split(","))

    for provider_priority, provider in enumerate(
        configured_order,
        start=1,
    ):
        if provider not in HOSTED_AI_PROVIDERS:
            continue

        models = _model_bindings(
            settings=settings,
            provider=provider,
            loaded_ai_gateway=loaded_ai_gateway,
        )
        if not models:
            continue

        resolved_credentials = await _resolve_provider_credentials(
            settings=settings,
            resolver=resolver,
            provider=provider,
        )
        if not resolved_credentials:
            continue

        existing_provider = existing_by_provider.get(provider)
        existing_credentials = {
            item.profile_key: item
            for item in (existing_provider.credentials if existing_provider is not None else ())
        }

        provisional_credentials: list[CredentialBindingConfiguration] = []
        for index, resolved in resolved_credentials:
            profile_key = f"{provider.lower()}-key-{index}"
            existing = existing_credentials.get(profile_key)
            if existing is not None and existing.vault_reference == resolved.reference.to_uri():
                provisional_credentials.append(existing)
            else:
                provisional_credentials.append(
                    CredentialBindingConfiguration(
                        profile_key=profile_key,
                        vault_reference=resolved.reference.to_uri(),
                        bootstrap_managed=True,
                    )
                )

        retained_routes = _retained_validated_routes(
            existing_provider=existing_provider,
            credentials=tuple(provisional_credentials),
            models=models,
        )
        receipts_by_profile: dict[
            str,
            AIValidatedRouteConfiguration,
        ] = {}
        for route in retained_routes:
            receipts_by_profile.setdefault(
                route.credential_profile_key,
                route,
            )

        credentials = tuple(
            credential
            if credential.profile_key in receipts_by_profile
            else CredentialBindingConfiguration(
                profile_key=credential.profile_key,
                vault_reference=credential.vault_reference,
                bootstrap_managed=True,
            )
            for credential in provisional_credentials
        )

        providers.append(
            AIProviderRuntimeConfiguration(
                provider_key=provider,
                enabled=True,
                base_url=_provider_base_url(provider, settings),
                credentials=credentials,
                models=models,
                validated_routes=retained_routes,
                priority=provider_priority,
            )
        )
        print(
            "ai_route_refresh=CONFIGURED "
            f"provider={provider} credentials={len(credentials)} "
            f"models={len(models)} "
            f"retained_validated_routes={len(retained_routes)}"
        )

    integrations = RuntimeIntegrationsConfiguration(
        ai_providers=tuple(providers),
        data_sources=configuration.runtime_integrations.data_sources,
    )
    return configuration.model_copy(update={"runtime_integrations": integrations})


async def build_bootstrap_runtime_configuration(
    *,
    settings: Settings,
    resolver: SecretResolver,
    loaded_ai_gateway: LoadedAIGatewayConfiguration,
    configuration: ReturnPlatformConfiguration,
) -> ReturnPlatformConfiguration:
    """Validate pairs while preserving every configured runtime route."""

    mongo: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        settings.mongo_dsn.get_secret_value(),
        tz_aware=True,
    )
    collection = mongo[settings.mongo_database][_RECEIPT_COLLECTION]
    await collection.create_index("receipt_id", unique=True)
    await collection.create_index(
        [
            ("subject_key", 1),
            ("configuration_checksum", 1),
            ("secret_fingerprint", 1),
            ("secret_version", 1),
            ("valid_until", -1),
        ]
    )

    providers: list[AIProviderRuntimeConfiguration] = []
    configured_order = tuple(item.strip() for item in settings.ai_provider_order.split(","))
    try:
        for provider_priority, provider in enumerate(
            configured_order,
            start=1,
        ):
            if provider not in HOSTED_AI_PROVIDERS:
                continue

            models = _model_bindings(
                settings=settings,
                provider=provider,
                loaded_ai_gateway=loaded_ai_gateway,
            )
            if not models:
                continue

            resolved_credentials = await _resolve_provider_credentials(
                settings=settings,
                resolver=resolver,
                provider=provider,
            )
            if not resolved_credentials:
                continue

            validated_pairs: list[_ValidatedPair] = []
            for credential_index, resolved in resolved_credentials:
                for model in models:
                    task_keys = model.task_keys
                    cached_tests = (
                        await _cached_pair_tests(
                            collection=collection,
                            settings=settings,
                            resolved=resolved,
                            provider=provider,
                            model_id=model.model_id,
                            model_class=model.model_class,
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
                                base_url=_provider_base_url(
                                    provider,
                                    settings,
                                ),
                                api_key=resolved.value,
                                model_id=model.model_id,
                                settings=settings,
                            )
                        )
                    except Exception as exc:
                        print(
                            "ai_bootstrap_validation=FAILED "
                            f"provider={provider} "
                            f"credential={credential_index} "
                            f"model={model.model_id} "
                            f"error={type(exc).__name__}"
                        )
                        continue

                    validated_pairs.append(
                        _ValidatedPair(
                            credential_index=credential_index,
                            model_id=model.model_id,
                            model_class=model.model_class,
                            tests=tests,
                        )
                    )

            route_bindings: list[AIValidatedRouteConfiguration] = []
            credential_receipts: dict[int, tuple[str, str]] = {}
            resolved_by_index = dict(resolved_credentials)

            for pair in validated_pairs:
                resolved = resolved_by_index[pair.credential_index]
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
                        subject_type=("AI_CREDENTIAL_MODEL_BINDING"),
                        subject_key=(f"{provider}:{pair.model_id}:{task_key}"),
                        configuration_checksum=checksum,
                        fingerprint_key=(
                            settings.validation_fingerprint_key.get_secret_value().encode("utf-8")
                        ),
                        actor_id="runtime-bootstrap",
                    )
                    receipt = receipt.model_copy(
                        update={
                            "valid_until": (
                                receipt.verified_at
                                + timedelta(hours=(settings.ai_validation_interval_hours))
                            )
                        }
                    )
                    receipt = await _persist_or_reuse_receipt(
                        collection,
                        receipt,
                    )
                    receipt_id = str(receipt.receipt_id)
                    credential_receipts.setdefault(
                        pair.credential_index,
                        (receipt_id, checksum),
                    )
                    route_bindings.append(
                        AIValidatedRouteConfiguration(
                            credential_profile_key=(
                                f"{provider.lower()}-key-{pair.credential_index}"
                            ),
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
                    validation_receipt_id=(
                        credential_receipts[index][0] if index in credential_receipts else None
                    ),
                    validation_configuration_checksum=(
                        credential_receipts[index][1] if index in credential_receipts else None
                    ),
                    bootstrap_managed=index not in credential_receipts,
                )
                for index, resolved in resolved_credentials
            )

            providers.append(
                AIProviderRuntimeConfiguration(
                    provider_key=provider,
                    enabled=True,
                    base_url=_provider_base_url(provider, settings),
                    credentials=credentials,
                    models=models,
                    validated_routes=tuple(route_bindings),
                    priority=provider_priority,
                )
            )

            validated_credentials, validated_models = _project_validated_pairs(validated_pairs)
            state = "PASSED" if route_bindings else "DEGRADED"
            print(
                f"ai_bootstrap_validation={state} "
                f"provider={provider} "
                f"configured_credentials={len(credentials)} "
                f"configured_models={len(models)} "
                f"validated_credentials={len(validated_credentials)} "
                f"validated_models={len(validated_models)} "
                f"validated_routes={len(route_bindings)}"
            )
    finally:
        await mongo.close()

    integrations = RuntimeIntegrationsConfiguration(
        ai_providers=tuple(providers),
        data_sources=configuration.runtime_integrations.data_sources,
    )
    return configuration.model_copy(update={"runtime_integrations": integrations})
