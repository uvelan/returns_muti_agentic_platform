from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr

from return_platform.ai.routing.tasks import load_ai_gateway_configuration
from return_platform.configuration.cli import bootstrap_graph_configuration
from return_platform.configuration.return_configuration import load_return_configuration
from return_platform.configuration.settings import (
    DEFAULT_AI_GATEWAY_CONFIGURATION_PATH,
    DEFAULT_DEPENDENCY_SIMULATION_CONFIGURATION_PATH,
    DEFAULT_RETURN_CONFIGURATION_PATH,
)
from return_platform.configuration.snapshot import (
    AI_GATEWAY_DOMAIN_KEY,
    DEPENDENCY_SIMULATION_DOMAIN_KEY,
    RETURN_PLATFORM_DOMAIN_KEY,
)
from return_platform.dependency_simulation.configuration import (
    load_dependency_simulation_configuration,
)


def _expected_return_platform_baseline(payload: dict[str, Any]) -> dict[str, str]:
    """The RETURN_PLATFORM `PACKAGED_KEY_DIGESTS` baseline `main()` should record.

    CFG-6: `deployment` is a `CARRY_FORWARD_SPLIT_KEYS` entry now (like
    `AI_GATEWAY`'s `tasks` and `DEPENDENCY_SIMULATION`'s `dependencies`), so
    the baseline is keyed by UNIT -- `deployment.ai`, `deployment.dependencies`,
    `deployment.feedback_learning`, `deployment.support_ticket` -- not by the
    single top-level key `deployment`. Every other RETURN_PLATFORM key is
    still one unit, so this is a superset of the pre-CFG-6
    `bootstrap_graph_configuration._key_digests(payload)` answer.
    """
    split_keys = bootstrap_graph_configuration.CARRY_FORWARD_SPLIT_KEYS[RETURN_PLATFORM_DOMAIN_KEY]
    return bootstrap_graph_configuration._key_digests(
        bootstrap_graph_configuration._units(payload, split_keys)
    )


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
        domains: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.active_payload = active_payload
        # The AI gateway and dependency simulation domains the active release
        # carries, when a test needs them; absent, the release has none and the
        # packaged files publish as they are.
        self.domains = dict(domains or {})
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
        if domain_key == RETURN_PLATFORM_DOMAIN_KEY:
            return self.active_payload
        return self.domains.get(domain_key)

    async def get_all_domain_configs(self, _release_id: str) -> dict[str, Any]:
        return {RETURN_PLATFORM_DOMAIN_KEY: self.active_payload, **self.domains}

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
async def test_a_key_the_model_retired_is_dropped_from_the_carried_release(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """CFG-1 (D-CFG-2) retired `feature_flags`/`extensions` from the model.

    An active release published before that still carries them.
    `_carry_forward`'s last loop (`merged.setdefault(key, value)` over the
    active release) puts them straight back into the merged payload every
    time, and without a drop `ReturnPlatformConfiguration.model_validate`
    (`StrictConfigModel` forbids extra keys) would refuse the release on
    every bootstrap from here on -- the `except ValidationError` fallback a
    few lines below discards every operator value on the release, which is
    the deadlock this test proves does not happen.
    """
    packaged = load_return_configuration(DEFAULT_RETURN_CONFIGURATION_PATH).configuration
    older_release_payload = packaged.model_dump(mode="json")
    older_release_payload["feature_flags"] = {
        "reusable_conversation_engine": True,
        "order_discovery_copilot": True,
        "copilot_operations_console": False,
        "graph_first_runtime_configuration": True,
    }
    older_release_payload["extensions"] = {
        "document_artifact_metadata": True,
        "ocr_processing": False,
        "image_processing": False,
        "ncr_workflow": False,
        "vendor_recovery_workflow": True,
    }
    # An unrelated operator edit, carried in the same release, so this also
    # proves the drop touches only the retired keys.
    older_release_payload["bay"]["require_physical_receipt"] = not older_release_payload["bay"][
        "require_physical_receipt"
    ]
    repository = _CarryForwardRepository(older_release_payload)
    _install_bootstrap_doubles(monkeypatch, repository)

    with caplog.at_level("WARNING"):
        await bootstrap_graph_configuration.main()

    assert "retired_configuration_key key=extensions" in caplog.text
    assert "retired_configuration_key key=feature_flags" in caplog.text
    published = next(iter(repository.saved.values()))[RETURN_PLATFORM_DOMAIN_KEY]
    assert "feature_flags" not in published
    assert "extensions" not in published
    assert (
        published["bay"]["require_physical_receipt"]
        == (older_release_payload["bay"]["require_physical_receipt"])
    )


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
    recorded = repository.written_metadata[release_id]
    assert recorded[bootstrap_graph_configuration.PACKAGED_KEY_DIGESTS] == (
        _expected_return_platform_baseline(packaged_payload)
    )
    # The other two domains record their own baseline beside it.
    assert set(recorded[bootstrap_graph_configuration.PACKAGED_DOMAIN_KEY_DIGESTS]) == {
        "AI_GATEWAY",
        "DEPENDENCY_SIMULATION",
    }


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

    release_id = next(iter(repository.saved))
    published = repository.saved[release_id][RETURN_PLATFORM_DOMAIN_KEY]
    assert published["discovery"]["identification_fields"] == []
    assert "packaged_configuration_not_adopted" in caplog.text
    assert "discovery" in caplog.text
    # No baseline is invented for the key that lost: stamping the current file
    # onto a value that is dropping that file's change would mark it as an
    # operator edit and freeze it out permanently. Every other key is decided --
    # the release carries exactly what the file says -- so their baseline IS
    # recorded, and the deployment leaves the undecidable path for all of them
    # on this run rather than never. Before this was per key, a single
    # undecidable key kept the whole file's baseline from ever being recorded,
    # and "at most once" was in practice "on every start, forever".
    recorded = repository.written_metadata[release_id][
        bootstrap_graph_configuration.PACKAGED_KEY_DIGESTS
    ]
    expected = _expected_return_platform_baseline(packaged_payload)
    assert "discovery" not in recorded
    assert recorded == {key: digest for key, digest in expected.items() if key != "discovery"}


@pytest.mark.asyncio
async def test_a_leaf_the_release_lacks_is_filled_even_inside_an_undecidable_key(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Observed 2026-09-11 on a deployment whose releases had no baseline: the
    packaged file had gained `agents.support_response` and five
    `return_policy.return_method_derivation.ship_via_methods` codes weeks
    earlier, and no release ever carried them, because the whole top-level key
    lost the moment any leaf inside it disagreed. A leaf the release does not
    carry at all cannot be anyone's edit, so it is adopted; a leaf both carry
    stays the release's, and the key is still named as undecided."""
    packaged = load_return_configuration(DEFAULT_RETURN_CONFIGURATION_PATH).configuration
    packaged_payload = packaged.model_dump(mode="json")
    older = {**packaged_payload, "agents": {**packaged_payload["agents"]}}
    dropped_agent, dropped_block = next(iter(packaged_payload["agents"].items()))
    del older["agents"][dropped_agent]
    edited_agent = next(name for name in older["agents"])
    older["agents"][edited_agent] = {
        **older["agents"][edited_agent],
        "timeout_seconds": 4242,
    }
    repository = _CarryForwardRepository(older)
    _install_bootstrap_doubles(monkeypatch, repository)

    with caplog.at_level("WARNING"):
        await bootstrap_graph_configuration.main()

    release_id = next(iter(repository.saved))
    published = repository.saved[release_id][RETURN_PLATFORM_DOMAIN_KEY]
    assert published["agents"][dropped_agent] == dropped_block
    assert published["agents"][edited_agent]["timeout_seconds"] == 4242
    assert "keys=agents" in caplog.text
    assert (
        "agents"
        not in repository.written_metadata[release_id][
            bootstrap_graph_configuration.PACKAGED_KEY_DIGESTS
        ]
    )


@pytest.mark.asyncio
async def test_a_deleted_entry_the_file_still_carries_is_named_not_stamped_decided(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """RV finding F1 on CFG-0. An operator deleted a ship-via code through the
    Business tab; the file still carries it; the release has no baseline for
    `return_policy`. The entry comes back (nothing can tell it from a code the
    file gained), but the key is named as undecided and no baseline is recorded
    for it -- before this, a pure deletion made the filled key equal to the
    file, so nothing was named and the key was stamped decided."""
    packaged = load_return_configuration(DEFAULT_RETURN_CONFIGURATION_PATH).configuration
    packaged_payload = packaged.model_dump(mode="json")
    older = {**packaged_payload, "return_policy": {**packaged_payload["return_policy"]}}
    derivation = dict(older["return_policy"]["return_method_derivation"])
    methods = dict(derivation["ship_via_methods"])
    deleted_code = next(iter(methods))
    del methods[deleted_code]
    derivation["ship_via_methods"] = methods
    older["return_policy"]["return_method_derivation"] = derivation
    repository = _CarryForwardRepository(older)
    _install_bootstrap_doubles(monkeypatch, repository)

    with caplog.at_level("WARNING"):
        await bootstrap_graph_configuration.main()

    release_id = next(iter(repository.saved))
    published = repository.saved[release_id][RETURN_PLATFORM_DOMAIN_KEY]
    assert (
        deleted_code in published["return_policy"]["return_method_derivation"]["ship_via_methods"]
    )
    assert "return_policy" in caplog.text
    assert (
        "return_policy"
        not in repository.written_metadata[release_id][
            bootstrap_graph_configuration.PACKAGED_KEY_DIGESTS
        ]
    )


def test_assemble_keeps_a_split_key_whose_value_is_not_a_mapping() -> None:
    units = bootstrap_graph_configuration._units(
        {"tasks": "not-a-mapping", "retry": {}}, ("tasks",)
    )
    assert bootstrap_graph_configuration._assemble(units, ("tasks",)) == {
        "tasks": "not-a-mapping",
        "retry": {},
    }


@pytest.mark.asyncio
async def test_a_partial_baseline_decides_its_keys_and_leaves_the_rest_undecided(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The second run after the one above: `support` was recorded, `discovery`
    was not. A packaged change to `support` is adopted from the baseline; the
    still-baseline-less `discovery` keeps the release's value and is named
    again rather than silently stamped as an operator edit."""
    packaged = load_return_configuration(DEFAULT_RETURN_CONFIGURATION_PATH).configuration
    packaged_payload = packaged.model_dump(mode="json")
    older_support = {**packaged_payload["support"], "queues": ["OLD-PACKAGED-QUEUE"]}
    older_discovery = {**packaged_payload["discovery"], "identification_fields": []}
    older = {**packaged_payload, "support": older_support, "discovery": older_discovery}
    partial = _baseline_of({"support": older_support})
    repository = _CarryForwardRepository(older, metadata=partial)
    _install_bootstrap_doubles(monkeypatch, repository)

    with caplog.at_level("WARNING"):
        await bootstrap_graph_configuration.main()

    release_id = next(iter(repository.saved))
    published = repository.saved[release_id][RETURN_PLATFORM_DOMAIN_KEY]
    assert published["support"] == packaged_payload["support"]
    assert published["discovery"]["identification_fields"] == []
    assert "keys=discovery" in caplog.text
    recorded = repository.written_metadata[release_id][
        bootstrap_graph_configuration.PACKAGED_KEY_DIGESTS
    ]
    assert "support" in recorded
    assert "discovery" not in recorded


@pytest.mark.asyncio
async def test_adopt_packaged_key_takes_the_file_for_one_key_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The per-key answer to the warning: taking the file for `discovery` must
    not also take it for the `support` an operator edited."""
    packaged = load_return_configuration(DEFAULT_RETURN_CONFIGURATION_PATH).configuration
    packaged_payload = packaged.model_dump(mode="json")
    operator_queues = ["OPERATOR-EDITED-QUEUE"]
    older = {
        **packaged_payload,
        "discovery": {**packaged_payload["discovery"], "identification_fields": []},
        "support": {**packaged_payload["support"], "queues": operator_queues},
    }
    repository = _CarryForwardRepository(older)
    _install_bootstrap_doubles(monkeypatch, repository)

    await bootstrap_graph_configuration.main(adopt_packaged_keys=("discovery",))

    release_id = next(iter(repository.saved))
    published = repository.saved[release_id][RETURN_PLATFORM_DOMAIN_KEY]
    assert published["discovery"] == packaged_payload["discovery"]
    assert published["support"]["queues"] == operator_queues
    recorded = repository.written_metadata[release_id][
        bootstrap_graph_configuration.PACKAGED_KEY_DIGESTS
    ]
    assert "discovery" in recorded
    assert "support" not in recorded


@pytest.mark.asyncio
async def test_adopt_packaged_key_refuses_a_key_the_file_does_not_have(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packaged = load_return_configuration(DEFAULT_RETURN_CONFIGURATION_PATH).configuration
    repository = _CarryForwardRepository(packaged.model_dump(mode="json"))
    _install_bootstrap_doubles(monkeypatch, repository)

    with pytest.raises(ValueError, match="no_such_key"):
        await bootstrap_graph_configuration.main(adopt_packaged_keys=("no_such_key",))
    assert repository.saved == {}


@pytest.mark.asyncio
async def test_an_unknown_adopt_packaged_key_refuses_before_any_write_with_no_active_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RV F5 on CFG-0: the refusal must be symmetric.

    A qualified key naming an unknown domain (`--adopt-packaged-key
    BOGUS/x`) always failed in `_adopt_requests`, before any write,
    regardless of whether an active release exists. A bare key naming a unit
    of another domain (the operator forgot the `DOMAIN/` prefix) used to be
    checked only inside the active-release carry-forward branch -- so on a
    first boot, with no active release to check it against, it silently did
    nothing instead of failing. Both now fail the same way, unconditionally.
    """
    packaged = load_return_configuration(DEFAULT_RETURN_CONFIGURATION_PATH).configuration
    repository = _CarryForwardRepository(packaged.model_dump(mode="json"))

    async def no_active_release() -> None:
        return None

    monkeypatch.setattr(repository, "get_active_release", no_active_release)
    _install_bootstrap_doubles(monkeypatch, repository)

    with pytest.raises(ValueError, match="no_such_key"):
        await bootstrap_graph_configuration.main(adopt_packaged_keys=("no_such_key",))
    assert repository.saved == {}


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
    assert repository.written_metadata[release_id][
        bootstrap_graph_configuration.PACKAGED_KEY_DIGESTS
    ] == _expected_return_platform_baseline(packaged_payload)


# --- the AI gateway and dependency simulation domains ------------------------


_EDITED_TASK = "RETURN_STATUS_SUMMARY_V1"


def _packaged_ai_gateway() -> dict[str, Any]:
    return load_ai_gateway_configuration(
        DEFAULT_AI_GATEWAY_CONFIGURATION_PATH
    ).configuration.model_dump(mode="json")


def _packaged_dependency_simulation() -> dict[str, Any]:
    return load_dependency_simulation_configuration(
        DEFAULT_DEPENDENCY_SIMULATION_CONFIGURATION_PATH
    ).configuration.model_dump(mode="json")


def _with_task_edit(ai_gateway: dict[str, Any], **fields: Any) -> dict[str, Any]:
    edited = {**ai_gateway, "tasks": {**ai_gateway["tasks"]}}
    edited["tasks"][_EDITED_TASK] = {**edited["tasks"][_EDITED_TASK], **fields}
    return edited


@pytest.mark.asyncio
async def test_an_operators_ai_task_edit_survives_the_next_start(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Observed 2026-09-11: `maximumOutputTokens` set to 1234 through the config
    API, and one bootstrap run later it read 192 -- the file's value -- because
    the AI gateway domain was always published from `ai_gateway.yaml` whole.
    Every edit made in the AI Control Center went back to the file on the next
    stack start. The task is carried forward as its own unit now."""
    packaged = load_return_configuration(DEFAULT_RETURN_CONFIGURATION_PATH).configuration
    packaged_ai = _packaged_ai_gateway()
    edited_ai = _with_task_edit(packaged_ai, maximumOutputTokens=1234)
    repository = _CarryForwardRepository(
        packaged.model_dump(mode="json"), domains={AI_GATEWAY_DOMAIN_KEY: edited_ai}
    )
    _install_bootstrap_doubles(monkeypatch, repository)

    with caplog.at_level("WARNING"):
        await bootstrap_graph_configuration.main()

    release_id = next(iter(repository.saved))
    published = repository.saved[release_id][AI_GATEWAY_DOMAIN_KEY]
    assert published["tasks"][_EDITED_TASK]["maximumOutputTokens"] == 1234
    untouched = {task: body for task, body in published["tasks"].items() if task != _EDITED_TASK}
    assert untouched == {t: b for t, b in packaged_ai["tasks"].items() if t != _EDITED_TASK}
    assert f"domain={AI_GATEWAY_DOMAIN_KEY} keys=tasks.{_EDITED_TASK}" in caplog.text
    domain_baseline = repository.written_metadata[release_id][
        bootstrap_graph_configuration.PACKAGED_DOMAIN_KEY_DIGESTS
    ][AI_GATEWAY_DOMAIN_KEY]
    assert f"tasks.{_EDITED_TASK}" not in domain_baseline
    assert "circuitBreaker" in domain_baseline


@pytest.mark.asyncio
async def test_a_packaged_change_to_an_unedited_task_is_adopted_beside_an_edited_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per task, not per domain: the baseline decides `tasks.<id>` one by one,
    so an edited task does not freeze the file's changes to the others."""
    packaged = load_return_configuration(DEFAULT_RETURN_CONFIGURATION_PATH).configuration
    packaged_ai = _packaged_ai_gateway()
    other_task = next(task for task in packaged_ai["tasks"] if task != _EDITED_TASK)
    # The release: one task the operator edited, another still as the file was
    # when the release was cut -- and the file has since changed that one.
    older_ai = _with_task_edit(packaged_ai, maximumOutputTokens=1234)
    older_ai["tasks"][other_task] = {**older_ai["tasks"][other_task], "promptVersion": "older-v0"}
    older_units = bootstrap_graph_configuration._units(
        {
            **packaged_ai,
            "tasks": {**packaged_ai["tasks"], other_task: older_ai["tasks"][other_task]},
        },
        ("tasks",),
    )
    metadata = {
        bootstrap_graph_configuration.PACKAGED_DOMAIN_KEY_DIGESTS: {
            AI_GATEWAY_DOMAIN_KEY: bootstrap_graph_configuration._key_digests(older_units)
        }
    }
    repository = _CarryForwardRepository(
        packaged.model_dump(mode="json"),
        metadata=metadata,
        domains={AI_GATEWAY_DOMAIN_KEY: older_ai},
    )
    _install_bootstrap_doubles(monkeypatch, repository)

    await bootstrap_graph_configuration.main()

    release_id = next(iter(repository.saved))
    published = repository.saved[release_id][AI_GATEWAY_DOMAIN_KEY]
    assert published["tasks"][_EDITED_TASK]["maximumOutputTokens"] == 1234
    assert published["tasks"][other_task] == packaged_ai["tasks"][other_task]


@pytest.mark.asyncio
async def test_adopt_packaged_key_takes_the_file_for_one_ai_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packaged = load_return_configuration(DEFAULT_RETURN_CONFIGURATION_PATH).configuration
    packaged_ai = _packaged_ai_gateway()
    repository = _CarryForwardRepository(
        packaged.model_dump(mode="json"),
        domains={AI_GATEWAY_DOMAIN_KEY: _with_task_edit(packaged_ai, maximumOutputTokens=1234)},
    )
    _install_bootstrap_doubles(monkeypatch, repository)

    await bootstrap_graph_configuration.main(
        adopt_packaged_keys=(f"{AI_GATEWAY_DOMAIN_KEY}/tasks.{_EDITED_TASK}",)
    )

    release_id = next(iter(repository.saved))
    published = repository.saved[release_id][AI_GATEWAY_DOMAIN_KEY]
    assert published == packaged_ai
    domain_baseline = repository.written_metadata[release_id][
        bootstrap_graph_configuration.PACKAGED_DOMAIN_KEY_DIGESTS
    ][AI_GATEWAY_DOMAIN_KEY]
    assert f"tasks.{_EDITED_TASK}" in domain_baseline


@pytest.mark.asyncio
async def test_an_operators_dependency_simulation_edit_survives_the_next_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packaged = load_return_configuration(DEFAULT_RETURN_CONFIGURATION_PATH).configuration
    packaged_sim = _packaged_dependency_simulation()
    edited_sim = {**packaged_sim, "ai": {**packaged_sim["ai"], "temperature": 0.7}}
    repository = _CarryForwardRepository(
        packaged.model_dump(mode="json"),
        domains={DEPENDENCY_SIMULATION_DOMAIN_KEY: edited_sim},
    )
    _install_bootstrap_doubles(monkeypatch, repository)

    await bootstrap_graph_configuration.main()

    release_id = next(iter(repository.saved))
    published = repository.saved[release_id][DEPENDENCY_SIMULATION_DOMAIN_KEY]
    assert published["ai"]["temperature"] == 0.7
    assert published["dependencies"] == packaged_sim["dependencies"]


@pytest.mark.asyncio
async def test_a_release_whose_ai_domain_matches_the_file_is_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Carrying the domains forward must not turn an identical release into a
    publish: the comparison the UNCHANGED path makes still holds."""
    packaged = load_return_configuration(DEFAULT_RETURN_CONFIGURATION_PATH).configuration
    packaged_payload = packaged.model_dump(mode="json")
    repository = _CarryForwardRepository(
        packaged_payload,
        metadata=_baseline_of(packaged_payload),
        domains={
            AI_GATEWAY_DOMAIN_KEY: _packaged_ai_gateway(),
            DEPENDENCY_SIMULATION_DOMAIN_KEY: _packaged_dependency_simulation(),
        },
    )
    _install_bootstrap_doubles(monkeypatch, repository)

    await bootstrap_graph_configuration.main()

    assert repository.saved == {}
    assert (
        bootstrap_graph_configuration.PACKAGED_DOMAIN_KEY_DIGESTS
        in (repository.written_metadata["active-release-1"])
    )
