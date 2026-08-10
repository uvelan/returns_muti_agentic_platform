"""A release that changed after validation cannot be published.

Wave D3, second slice of making the graph lifecycle authoritative.

`checksum_sha256` existed and was recomputed on promotion, which reads like
verification and is not. Both repositories computed the checksum and
**overwrote** it, so whatever the domains said at publication time became the
recorded checksum. A domain edited between validation and publication was
blessed rather than caught, and an operator reading the field would reasonably
have believed otherwise.

The lifecycle now has a freeze point. `save_draft_domain` already refuses to
touch anything past DRAFT, so:

* **DRAFT → VALIDATED** computes and records the checksum. Contents are frozen.
* **VALIDATED → RELEASED** recomputes and *compares*, raising
  `ConfigurationIntegrityError` on mismatch.

Tampering is simulated by writing to the repository's private state, which is
the point: the public API cannot edit a VALIDATED release, so a test that went
through the public API could not produce the condition this guards against.
"""

from __future__ import annotations

import copy
import hashlib

import pytest

from return_platform.configuration.domain.errors import ConfigurationIntegrityError
from return_platform.configuration.graph_repository import (
    ConfigurationDomainNode,
    InMemoryConfigurationGraphRepository,
    compute_release_checksum,
)


async def _validated_release(release_id: str = "r-1") -> InMemoryConfigurationGraphRepository:
    repository = InMemoryConfigurationGraphRepository()
    await repository.save_draft_domain(release_id, "returns", {"retention_days": 30}, "tester")
    await repository.save_draft_domain(release_id, "ai_gateway", {"timeout": 5}, "tester")
    await repository.promote_release(release_id, "VALIDATED", "tester")
    return repository


@pytest.mark.asyncio
async def test_validation_records_a_checksum() -> None:
    """The freeze point. Before this the field could still be empty at
    VALIDATED, because only publication recomputed it."""
    repository = await _validated_release()

    release = await repository.get_release("r-1")

    assert release is not None
    assert len(release.checksum_sha256) == 64


@pytest.mark.asyncio
async def test_an_untouched_release_publishes() -> None:
    """The check must not refuse the ordinary path -- a verification that fails
    closed on everything is indistinguishable from a broken publish."""
    repository = await _validated_release()

    published = await repository.promote_release(
        "r-1", "RELEASED", "tester", expected_head_revision=0
    )

    assert published.status == "RELEASED"


@pytest.mark.asyncio
async def test_a_domain_edited_after_validation_cannot_be_published() -> None:
    """The defect this slice closes. Previously the recompute silently adopted
    the tampered content and the release published."""
    repository = await _validated_release()
    # Direct state write: `save_draft_domain` refuses a VALIDATED release, so
    # the public API cannot reach this condition -- which is exactly why the
    # recompute-and-overwrite made it invisible.
    repository._domains[("r-1", "returns")] = ConfigurationDomainNode(
        domain_key="returns", payload={"retention_days": 3650}, version=2, updated_by="attacker"
    )

    with pytest.raises(ConfigurationIntegrityError, match="changed after it was validated"):
        await repository.promote_release("r-1", "RELEASED", "tester", expected_head_revision=0)


@pytest.mark.asyncio
async def test_a_domain_added_after_validation_cannot_be_published() -> None:
    """Addition, not just modification. A checksum over only the domains it
    already knew about would miss a smuggled-in extra one."""
    repository = await _validated_release()
    repository._domains[("r-1", "extra")] = ConfigurationDomainNode(
        domain_key="extra", payload={"anything": True}, version=1, updated_by="attacker"
    )

    with pytest.raises(ConfigurationIntegrityError):
        await repository.promote_release("r-1", "RELEASED", "tester", expected_head_revision=0)


@pytest.mark.asyncio
async def test_a_domain_removed_after_validation_cannot_be_published() -> None:
    repository = await _validated_release()
    del repository._domains[("r-1", "ai_gateway")]

    with pytest.raises(ConfigurationIntegrityError):
        await repository.promote_release("r-1", "RELEASED", "tester", expected_head_revision=0)


@pytest.mark.asyncio
async def test_the_refusal_names_both_checksums() -> None:
    """An integrity failure is the kind of thing someone investigates months
    later. "Checksum mismatch" alone leaves them nothing to compare."""
    repository = await _validated_release()
    recorded = (await repository.get_release("r-1")).checksum_sha256  # type: ignore[union-attr]
    repository._domains[("r-1", "returns")] = ConfigurationDomainNode(
        domain_key="returns", payload={"retention_days": 1}, version=2, updated_by="attacker"
    )

    with pytest.raises(ConfigurationIntegrityError) as excinfo:
        await repository.promote_release("r-1", "RELEASED", "tester", expected_head_revision=0)

    assert recorded in str(excinfo.value)


@pytest.mark.asyncio
async def test_the_tampered_release_is_left_in_validated() -> None:
    """Refusing must not half-publish. If the status advanced before the check,
    a refused release would sit in a state with no way back."""
    repository = await _validated_release()
    repository._domains[("r-1", "returns")] = ConfigurationDomainNode(
        domain_key="returns", payload={"retention_days": 1}, version=2, updated_by="attacker"
    )

    with pytest.raises(ConfigurationIntegrityError):
        await repository.promote_release("r-1", "RELEASED", "tester", expected_head_revision=0)

    release = await repository.get_release("r-1")
    assert release is not None
    assert release.status == "VALIDATED"
    assert await repository.get_active_release() is None


# ---------------------------------------------------------------------------
# The shared hashing helper
# ---------------------------------------------------------------------------


def test_the_checksum_ignores_domain_iteration_order() -> None:
    """Both repositories feed this from different sources -- a dict in one, an
    ordered Cypher result in the other. If order mattered they would disagree
    for identical content."""
    pairs = [("b", '{"x":1}'), ("a", '{"y":2}')]

    assert compute_release_checksum(pairs) == compute_release_checksum(reversed(pairs))


def test_the_checksum_distinguishes_key_from_payload() -> None:
    """Concatenating without separation would make `("ab", "c")` and
    `("a", "bc")` hash identically -- a boundary a naive implementation misses
    and no realistic payload would ever reveal."""
    assert compute_release_checksum([("ab", "c")]) != compute_release_checksum([("a", "bc")])


def test_an_empty_release_still_has_a_stable_checksum() -> None:
    """A release with no domains must hash to something reproducible rather than
    to the empty string, or the verify step could not tell "no domains" from
    "never validated"."""
    first = compute_release_checksum([])
    assert len(first) == 64
    assert first == compute_release_checksum([])


@pytest.mark.asyncio
async def test_both_repositories_agree_on_the_hash_input() -> None:
    """The in-memory repository serialises payloads itself; Neo4j hashes the
    `payload_json` it stored. Both go through one helper now, and this pins that
    they are fed equivalent bytes for the same logical content."""
    payload = {"b": 2, "a": 1}
    repository = InMemoryConfigurationGraphRepository()
    await repository.save_draft_domain("r-2", "returns", copy.deepcopy(payload), "tester")
    await repository.promote_release("r-2", "VALIDATED", "tester")

    release = await repository.get_release("r-2")

    assert release is not None
    # `sort_keys=True` is what Neo4j's `save_draft_domain` writes, so this is the
    # byte sequence the Neo4j repository would hash for the same payload.
    assert release.checksum_sha256 == compute_release_checksum([("returns", '{"a": 1, "b": 2}')])
    # Not the raw concatenation -- the encoding is length-delimited so that
    # ("ab", "c") and ("a", "bc") cannot collide.
    assert release.checksum_sha256 != hashlib.sha256(b'returns{"a": 1, "b": 2}').hexdigest()
