"""`Neo4jConfigurationGraphRepository.get_domain_version` against real Neo4j.

RV CFG-3a round 1 F6: the optimistic lock's Cypher had zero coverage in any
suite, including `live_infra` -- only the in-memory repository was tested.
RV verified the implementation directly against the dev graph as part of
the review; this is that verification, made durable. Not named
`*_real_infra.py`, so `pytest.mark.live_infra` is what keeps it out of the
default run (`scripts/dev/run_real_infra_suite.sh` selects it back in).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from neo4j import AsyncGraphDatabase

from return_platform.configuration.graph_repository import Neo4jConfigurationGraphRepository
from return_platform.configuration.settings import Settings

pytestmark = [pytest.mark.asyncio, pytest.mark.live_infra]

_RELEASE_ID = "cfg-3a-rv-get-domain-version-probe"
_DOMAIN_KEY = "RETURN_PLATFORM"


@pytest.fixture
async def repository(test_settings: Settings) -> AsyncIterator[Neo4jConfigurationGraphRepository]:
    driver = AsyncGraphDatabase.driver(
        test_settings.neo4j_uri,
        auth=(test_settings.neo4j_user, test_settings.neo4j_password.get_secret_value()),
    )
    try:
        await driver.verify_connectivity()
        yield Neo4jConfigurationGraphRepository(driver)
    finally:
        # Leave no trace: this release/domain exists only for this test.
        async with driver.session() as session:
            await session.run(
                "MATCH (r:ConfigurationRelease {release_id: $release_id}) "
                "OPTIONAL MATCH (r)-[:HAS_DOMAIN]->(d:ConfigurationDomain) "
                "DETACH DELETE r, d",
                release_id=_RELEASE_ID,
            )
        await driver.close()


async def test_get_domain_version_tracks_saves_and_is_none_when_absent(
    repository: Neo4jConfigurationGraphRepository,
) -> None:
    assert await repository.get_domain_version(_RELEASE_ID, _DOMAIN_KEY) is None

    await repository.save_draft_domain(_RELEASE_ID, _DOMAIN_KEY, {"probe": 1}, actor_id="rv-cfg-3a")
    assert await repository.get_domain_version(_RELEASE_ID, _DOMAIN_KEY) == 1

    await repository.save_draft_domain(_RELEASE_ID, _DOMAIN_KEY, {"probe": 2}, actor_id="rv-cfg-3a")
    assert await repository.get_domain_version(_RELEASE_ID, _DOMAIN_KEY) == 2

    # A domain this release never carried, and a release that does not exist
    # at all -- both `None`, not an exception (`patch_domain_config`'s 404
    # path relies on `get_domain_config` for that; this method's contract is
    # just "the version, or nothing to compare against").
    assert await repository.get_domain_version(_RELEASE_ID, "NOT_A_DOMAIN") is None
    assert await repository.get_domain_version("no-such-release", _DOMAIN_KEY) is None
