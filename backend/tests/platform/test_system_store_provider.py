"""`provider` is honoured, or the platform refuses to start.

Found while measuring the configuration-lifecycle decision (see
`docs/CONFIGURATION_RELEASE_LIFECYCLE_DECISION.md`): `system_store.yaml`
advertises `allowed_providers: [NEO4J, MONGODB, POSTGRESQL, SQLSERVER]`, but
`provider` was not a field on the bootstrap loader's payload model at all.
`extra="ignore"` dropped it at parse time, so a manifest declaring
`provider: POSTGRESQL` bootstrapped silently onto Mongo.

A configuration value read by nothing and contradicted by everything is worse
than no configuration value, because operators reasonably believe it.

This does **not** make the store portable. It makes the manifest honest about
what this build can serve. Real portability needs a provider-neutral gateway
contract -- today `IndexDefinition` carries `partial_filter_expression` and
`expire_after_seconds`, which are Mongo concepts.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from return_platform.platform.system_store.manifest_loader import (
    SUPPORTED_PROVIDERS,
    UnsupportedSystemStoreProvider,
    load_system_store_config,
)

_MANIFEST = Path(__file__).resolve().parents[2] / "config" / "platform" / "system_store.yaml"


def _write_manifest(tmp_path: Path, **payload_overrides: object) -> Path:
    """A manifest derived from the real one, so the test tracks its shape."""
    raw = yaml.safe_load(_MANIFEST.read_bytes())
    raw["payload"].update(payload_overrides)
    path = tmp_path / "system_store.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


def test_the_real_manifest_declares_a_provider_this_build_can_serve() -> None:
    """The shipped manifest must load. If someone changes `provider` to an
    aspirational value, this fails here rather than at a customer's startup."""
    assert load_system_store_config(_MANIFEST).provider.upper() in SUPPORTED_PROVIDERS


def test_an_unimplemented_provider_is_refused(tmp_path: Path) -> None:
    path = _write_manifest(tmp_path, provider="POSTGRESQL")

    with pytest.raises(UnsupportedSystemStoreProvider) as excinfo:
        load_system_store_config(path)

    message = str(excinfo.value)
    assert "POSTGRESQL" in message
    assert "MONGODB" in message, "the refusal should say what this build can serve"


def test_a_manifest_that_contradicts_itself_is_refused(tmp_path: Path) -> None:
    """`provider` outside its own `allowed_providers`. Whichever half is wrong,
    running is not the answer -- and the two failures are reported separately
    because the fixes differ."""
    path = _write_manifest(tmp_path, provider="MONGODB", allowed_providers=["NEO4J"])

    with pytest.raises(UnsupportedSystemStoreProvider, match="excludes it"):
        load_system_store_config(path)


def test_provider_matching_ignores_case_and_padding(tmp_path: Path) -> None:
    """A manifest is hand-edited YAML. Refusing ` mongodb ` would be a refusal
    about whitespace dressed up as one about capability."""
    path = _write_manifest(tmp_path, provider="  mongodb  ")

    assert load_system_store_config(path).provider == "  mongodb  ", (
        "the declared value is preserved verbatim; only the comparison normalises"
    )


def test_an_absent_provider_defaults_to_the_implemented_one(tmp_path: Path) -> None:
    """Older manifests predate the field. Defaulting to MONGODB is honest --
    it is what they were always running on."""
    raw = yaml.safe_load(_MANIFEST.read_bytes())
    raw["payload"].pop("provider", None)
    raw["payload"].pop("allowed_providers", None)
    path = tmp_path / "system_store.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")

    assert load_system_store_config(path).provider == "MONGODB"


def test_allowed_providers_still_records_the_intended_destination(tmp_path: Path) -> None:
    """The manifest may list backends that are not built yet -- that is a
    roadmap, and this check is deliberately not a reason to delete it. Only the
    *active* provider has to be serviceable."""
    path = _write_manifest(
        tmp_path,
        provider="MONGODB",
        allowed_providers=["NEO4J", "MONGODB", "POSTGRESQL", "SQLSERVER"],
    )

    config = load_system_store_config(path)

    assert config.allowed_providers == ["NEO4J", "MONGODB", "POSTGRESQL", "SQLSERVER"]
    assert set(config.allowed_providers) - SUPPORTED_PROVIDERS, (
        "this test is meaningless if the manifest stops listing unbuilt providers"
    )
