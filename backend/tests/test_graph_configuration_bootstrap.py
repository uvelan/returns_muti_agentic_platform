from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr

from return_platform.configuration.cli import bootstrap_graph_configuration
from return_platform.configuration.return_configuration import load_return_configuration
from return_platform.configuration.settings import (
    DEFAULT_AI_GATEWAY_CONFIGURATION_PATH,
    DEFAULT_DEPENDENCY_SIMULATION_CONFIGURATION_PATH,
    DEFAULT_RETURN_CONFIGURATION_PATH,
)
from return_platform.configuration.snapshot import RETURN_PLATFORM_DOMAIN_KEY


class _Driver:
    async def verify_connectivity(self) -> None:
        return None

    async def close(self) -> None:
        return None


class _Repository:
    async def get_active_release(self) -> SimpleNamespace:
        return SimpleNamespace(release_id="active-release-1")


@pytest.mark.asyncio
async def test_if_missing_reuses_active_release_without_ai_validation(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = SimpleNamespace(
        neo4j_uri="bolt://127.0.0.1:7687",
        neo4j_user="neo4j",
        neo4j_password=SecretStr("secret"),
    )

    async def resolve_settings(*_args: object, **_kwargs: object) -> tuple[object, object]:
        return settings, object()

    monkeypatch.setattr(
        bootstrap_graph_configuration,
        "resolve_runtime_settings_from_vault",
        resolve_settings,
    )
    monkeypatch.setattr(
        bootstrap_graph_configuration.AsyncGraphDatabase,
        "driver",
        lambda *_args, **_kwargs: _Driver(),
    )
    monkeypatch.setattr(
        bootstrap_graph_configuration,
        "Neo4jConfigurationGraphRepository",
        lambda _driver: _Repository(),
    )
    ai_bootstrap = AsyncMock(
        side_effect=AssertionError("AI bootstrap validation must not run"),
    )
    monkeypatch.setattr(
        bootstrap_graph_configuration,
        "build_bootstrap_runtime_configuration",
        ai_bootstrap,
    )

    await bootstrap_graph_configuration.main(if_missing=True)

    assert ai_bootstrap.await_count == 0
    assert "graph_configuration_status=EXISTING" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_ai_validation_is_skipped_without_explicit_flag(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configuration = SimpleNamespace()
    ai_bootstrap = AsyncMock(
        side_effect=AssertionError("live AI validation must be opt-in"),
    )
    monkeypatch.setattr(
        bootstrap_graph_configuration,
        "build_bootstrap_runtime_configuration",
        ai_bootstrap,
    )

    result = await bootstrap_graph_configuration._prepare_return_configuration(
        validate_ai=False,
        settings=SimpleNamespace(),
        resolver=SimpleNamespace(),
        loaded_ai_gateway=SimpleNamespace(),
        configuration=configuration,
    )

    assert result is configuration
    assert ai_bootstrap.await_count == 0
    assert "ai_bootstrap_validation=SKIPPED" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_ai_validation_runs_when_explicitly_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configuration = SimpleNamespace()
    validated = SimpleNamespace()
    ai_bootstrap = AsyncMock(return_value=validated)
    monkeypatch.setattr(
        bootstrap_graph_configuration,
        "build_bootstrap_runtime_configuration",
        ai_bootstrap,
    )

    result = await bootstrap_graph_configuration._prepare_return_configuration(
        validate_ai=True,
        settings=SimpleNamespace(),
        resolver=SimpleNamespace(),
        loaded_ai_gateway=SimpleNamespace(),
        configuration=configuration,
    )

    assert result is validated
    ai_bootstrap.assert_awaited_once()


class _CarryForwardRepository:
    """Just enough graph to watch what the publish writes.

    A release exists and is RELEASED, which is the state every deployment is in
    after its first boot -- and the state in which the carry-forward path runs.
    """

    def __init__(
        self,
        active_payload: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.active_payload = active_payload
        # Empty by default: that is a release published before releases recorded
        # a packaged baseline, which is the state every deployment upgrading into
        # this behaviour is in.
        self.metadata = metadata or {}
        self.saved: dict[str, dict[str, Any]] = {}
        self.promotions: list[tuple[str, str]] = []
        self.written_metadata: dict[str, dict[str, Any]] = {}

    async def get_active_release(self) -> SimpleNamespace:
        return SimpleNamespace(
            release_id="active-release-1", status="RELEASED", metadata=self.metadata
        )

    async def set_release_metadata(self, release_id: str, metadata: dict[str, Any]) -> None:
        self.written_metadata[release_id] = metadata

    async def get_domain_config(self, _release_id: str, domain_key: str) -> dict[str, Any] | None:
        return self.active_payload if domain_key == RETURN_PLATFORM_DOMAIN_KEY else None

    async def get_all_domain_configs(self, _release_id: str) -> dict[str, Any]:
        return {RETURN_PLATFORM_DOMAIN_KEY: self.active_payload}

    async def get_release(self, release_id: str) -> SimpleNamespace | None:
        if release_id not in self.saved:
            return None
        status = "VALIDATED" if ("VALIDATED", release_id) in self.promotions else "DRAFT"
        return SimpleNamespace(release_id=release_id, status=status)

    async def save_draft_domain(
        self,
        release_id: str,
        domain_key: str,
        payload: dict[str, Any],
        *,
        actor_id: str,
    ) -> None:
        self.saved.setdefault(release_id, {})[domain_key] = payload

    async def get_head_revision(self) -> int:
        return 7

    async def promote_release(
        self,
        release_id: str,
        target_status: str,
        *,
        actor_id: str,
        expected_head_revision: int | None = None,
    ) -> None:
        self.promotions.append((target_status, release_id))


def _bootstrap_settings() -> SimpleNamespace:
    return SimpleNamespace(
        neo4j_uri="bolt://127.0.0.1:7687",
        neo4j_user="neo4j",
        neo4j_password=SecretStr("secret"),
        return_configuration_path=DEFAULT_RETURN_CONFIGURATION_PATH,
        ai_gateway_configuration_path=DEFAULT_AI_GATEWAY_CONFIGURATION_PATH,
        dependency_simulation_configuration_path=(DEFAULT_DEPENDENCY_SIMULATION_CONFIGURATION_PATH),
    )


def _install_bootstrap_doubles(
    monkeypatch: pytest.MonkeyPatch,
    repository: _CarryForwardRepository,
) -> None:
    async def resolve_settings(*_args: object, **_kwargs: object) -> tuple[object, object]:
        return _bootstrap_settings(), object()

    monkeypatch.setattr(
        bootstrap_graph_configuration,
        "resolve_runtime_settings_from_vault",
        resolve_settings,
    )
    monkeypatch.setattr(
        bootstrap_graph_configuration.AsyncGraphDatabase,
        "driver",
        lambda *_args, **_kwargs: _Driver(),
    )
    monkeypatch.setattr(
        bootstrap_graph_configuration,
        "Neo4jConfigurationGraphRepository",
        lambda _driver: repository,
    )


@pytest.mark.asyncio
async def test_a_key_the_active_release_predates_is_adopted_from_the_packaged_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Otherwise a new setting can never reach a deployment.

    `copilot.order_discovery_agent_id` was added to the packaged YAML, the
    endpoint that serves it was correct, and `/api/runtime-config` still answered
    `null` -- because the active release predated the key, was carried forward
    whole, and republished the model default over the top of it.
    """
    packaged = load_return_configuration(DEFAULT_RETURN_CONFIGURATION_PATH).configuration
    older_release_payload = packaged.model_dump(mode="json")
    del older_release_payload["copilot"]
    repository = _CarryForwardRepository(older_release_payload)
    _install_bootstrap_doubles(monkeypatch, repository)

    await bootstrap_graph_configuration.main()

    published = next(iter(repository.saved.values()))[RETURN_PLATFORM_DOMAIN_KEY]
    assert published["copilot"] == {
        "order_discovery_agent_id": "order-discovery-agent",
        # The model default: an empty column list means the deployment has
        # not chosen candidate-table columns and the client falls back.
        "candidate_columns": [],
    }


@pytest.mark.asyncio
async def test_the_active_release_still_wins_for_every_key_it_carries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The merge must not undo an operator's edits, which is the whole reason
    the release is carried forward at all."""
    packaged = load_return_configuration(DEFAULT_RETURN_CONFIGURATION_PATH).configuration
    older_release_payload = packaged.model_dump(mode="json")
    del older_release_payload["copilot"]
    operator_queues = ["OPERATOR-EDITED-QUEUE"]
    older_release_payload["support"]["queues"] = operator_queues
    repository = _CarryForwardRepository(older_release_payload)
    _install_bootstrap_doubles(monkeypatch, repository)

    await bootstrap_graph_configuration.main()

    published = next(iter(repository.saved.values()))[RETURN_PLATFORM_DOMAIN_KEY]
    assert published["support"]["queues"] == operator_queues
    assert list(packaged.support.queues) != operator_queues


def _baseline_of(payload: dict[str, Any]) -> dict[str, Any]:
    """The metadata a release published from `payload` would have recorded."""
    return {
        bootstrap_graph_configuration.PACKAGED_KEY_DIGESTS: (
            bootstrap_graph_configuration._key_digests(payload)
        )
    }


@pytest.mark.asyncio
async def test_a_packaged_change_inside_a_key_the_release_carries_is_adopted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The defect the baseline exists to fix, at the granularity it actually bit.

    `{**packaged, **active}` delivers "keys the release predates come from the
    file" and nothing else: a key the release DOES carry always wins, so no
    change *inside* one could ever ship. `discovery` is one such key, so adding
    an identification field to the packaged YAML reached no deployment that had
    published a release -- and the run reported `UNCHANGED`, which reads as "your
    change is already live".

    Here the release carries a `discovery` from before that edit, and its
    baseline says the packaged file said the same thing at the time. Nobody
    edited it, so the file's newer value is what publishes.
    """
    packaged = load_return_configuration(DEFAULT_RETURN_CONFIGURATION_PATH).configuration
    packaged_payload = packaged.model_dump(mode="json")

    older_discovery = {**packaged_payload["discovery"], "identification_fields": []}
    older_packaged = {**packaged_payload, "discovery": older_discovery}
    repository = _CarryForwardRepository(
        dict(older_packaged), metadata=_baseline_of(older_packaged)
    )
    _install_bootstrap_doubles(monkeypatch, repository)

    await bootstrap_graph_configuration.main()

    published = next(iter(repository.saved.values()))[RETURN_PLATFORM_DOMAIN_KEY]
    assert published["discovery"] == packaged_payload["discovery"]
    assert published["discovery"]["identification_fields"] != []


@pytest.mark.asyncio
async def test_an_operator_edit_survives_the_packaged_file_that_disagrees_with_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other half, and the reason the baseline is needed rather than a rule.

    The release and the packaged file disagree about `support` in both this test
    and the one above. Nothing about the two payloads distinguishes the cases --
    only the baseline does: there the release still matched what the file said
    when it was cut, here it has moved away from it, so someone edited it.
    """
    packaged = load_return_configuration(DEFAULT_RETURN_CONFIGURATION_PATH).configuration
    packaged_payload = packaged.model_dump(mode="json")

    operator_queues = ["OPERATOR-EDITED-QUEUE"]
    edited = {
        **packaged_payload,
        "support": {**packaged_payload["support"], "queues": operator_queues},
    }
    repository = _CarryForwardRepository(edited, metadata=_baseline_of(packaged_payload))
    _install_bootstrap_doubles(monkeypatch, repository)

    await bootstrap_graph_configuration.main()

    published = next(iter(repository.saved.values()))[RETURN_PLATFORM_DOMAIN_KEY]
    assert published["support"]["queues"] == operator_queues
    assert list(packaged.support.queues) != operator_queues


@pytest.mark.asyncio
async def test_state_this_bootstrap_generated_is_not_overwritten_by_the_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An operator is not the only writer, and the baseline covers both.

    `runtime_integrations` is declared in the packaged YAML *and* filled in by a
    `--validate-ai` run, which writes AI receipts into the published payload
    after the file was read. Against the baseline that reads exactly like an
    operator edit -- moved away from what the file said -- which is the correct
    answer: a plain restart must not revert validated routes to the file's empty
    list.
    """
    packaged = load_return_configuration(DEFAULT_RETURN_CONFIGURATION_PATH).configuration
    packaged_payload = packaged.model_dump(mode="json")
    assert packaged_payload["runtime_integrations"]["ai_providers"] == []

    # Disabled, so the model's own "an enabled provider needs a credential and an
    # enabled model" rules do not apply -- what is under test is that the value
    # survives the merge, not what a valid route looks like.
    receipts = [
        {
            "provider_key": "ANTHROPIC",
            "enabled": False,
            "base_url": "https://api.anthropic.com",
            "credentials": [],
            "models": [],
            "validated_routes": [],
            "priority": 1,
        }
    ]
    with_receipts = {
        **packaged_payload,
        "runtime_integrations": {
            **packaged_payload["runtime_integrations"],
            "ai_providers": receipts,
        },
    }
    repository = _CarryForwardRepository(with_receipts, metadata=_baseline_of(packaged_payload))
    _install_bootstrap_doubles(monkeypatch, repository)

    await bootstrap_graph_configuration.main()

    published = next(iter(repository.saved.values()))[RETURN_PLATFORM_DOMAIN_KEY]
    assert published["runtime_integrations"]["ai_providers"] == receipts


@pytest.mark.asyncio
async def test_the_publish_records_the_baseline_it_was_built_from(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Digests of the PACKAGED values, never of the published ones.

    The published payload also carries what this command generates. Recording
    that as the baseline would mark generated state as matching the file, and the
    next run would overwrite it from the file -- the failure the test above
    guards against, reintroduced one publish later.
    """
    packaged = load_return_configuration(DEFAULT_RETURN_CONFIGURATION_PATH).configuration
    packaged_payload = packaged.model_dump(mode="json")
    older = {**packaged_payload, "discovery": {**packaged_payload["discovery"]}}
    older["discovery"]["identification_fields"] = []
    repository = _CarryForwardRepository(older, metadata=_baseline_of(older))
    _install_bootstrap_doubles(monkeypatch, repository)

    await bootstrap_graph_configuration.main()

    release_id = next(iter(repository.saved))
    assert repository.written_metadata[release_id] == _baseline_of(packaged_payload)


@pytest.mark.asyncio
async def test_a_release_with_no_baseline_keeps_its_values_and_says_which(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Undecidable is not the same as fine, and silence was the original defect.

    Without a baseline the two cases above are indistinguishable, so the release
    still wins -- overwriting an operator's edits on a restart would be the worse
    failure. What must not happen again is that the run reports `UNCHANGED` and
    lets the operator believe the packaged edit shipped.
    """
    packaged = load_return_configuration(DEFAULT_RETURN_CONFIGURATION_PATH).configuration
    packaged_payload = packaged.model_dump(mode="json")
    older = {**packaged_payload, "discovery": {**packaged_payload["discovery"]}}
    older["discovery"]["identification_fields"] = []
    repository = _CarryForwardRepository(older)
    _install_bootstrap_doubles(monkeypatch, repository)

    with caplog.at_level("WARNING"):
        await bootstrap_graph_configuration.main()

    published = next(iter(repository.saved.values()))[RETURN_PLATFORM_DOMAIN_KEY]
    assert published["discovery"]["identification_fields"] == []
    assert "packaged_configuration_not_adopted" in caplog.text
    assert "discovery" in caplog.text
    # No baseline is invented for it: stamping the current file onto a release
    # that is dropping that file's changes would mark them as operator edits and
    # freeze them out permanently.
    assert repository.written_metadata == {}


@pytest.mark.asyncio
async def test_adopt_packaged_is_the_operators_way_out_of_an_undecidable_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One run, and the release it publishes can decide for itself afterwards."""
    packaged = load_return_configuration(DEFAULT_RETURN_CONFIGURATION_PATH).configuration
    packaged_payload = packaged.model_dump(mode="json")
    older = {**packaged_payload, "discovery": {**packaged_payload["discovery"]}}
    older["discovery"]["identification_fields"] = []
    repository = _CarryForwardRepository(older)
    _install_bootstrap_doubles(monkeypatch, repository)

    await bootstrap_graph_configuration.main(adopt_packaged=True)

    release_id = next(iter(repository.saved))
    published = repository.saved[release_id][RETURN_PLATFORM_DOMAIN_KEY]
    assert published["discovery"] == packaged_payload["discovery"]
    assert repository.written_metadata[release_id] == _baseline_of(packaged_payload)
