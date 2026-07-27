"""Graph-backed configuration repository and release resolver models."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Protocol, cast, runtime_checkable

from neo4j import AsyncDriver
from pydantic import BaseModel, ConfigDict, Field

DEFAULT_CONFIGURATION_SCOPE = "production:global"


class ConfigurationRevisionConflict(RuntimeError):
    """Raised when a release publication loses an optimistic-concurrency race."""


class ConfigurationReleaseNode(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    release_id: str
    status: str = "DRAFT"  # DRAFT, VALIDATED, RELEASED, SUPERSEDED, ARCHIVED
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    created_by: str = "system"
    checksum_sha256: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConfigurationDomainNode(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    domain_key: str
    payload: dict[str, Any]
    version: int = 1
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_by: str = "system"


@runtime_checkable
class ConfigurationGraphRepository(Protocol):
    async def get_head_revision(self, scope_key: str = DEFAULT_CONFIGURATION_SCOPE) -> int: ...

    async def get_active_release(self) -> ConfigurationReleaseNode | None: ...

    async def get_release(self, release_id: str) -> ConfigurationReleaseNode | None: ...

    async def list_releases(self, limit: int = 20) -> list[ConfigurationReleaseNode]: ...

    async def get_domain_config(
        self, release_id: str, domain_key: str
    ) -> dict[str, Any] | None: ...

    async def get_all_domain_configs(self, release_id: str) -> dict[str, Any]: ...

    async def save_draft_domain(
        self, release_id: str, domain_key: str, payload: dict[str, Any], actor_id: str
    ) -> None: ...

    async def promote_release(
        self,
        release_id: str,
        status: str,
        actor_id: str,
        *,
        expected_head_revision: int | None = None,
        scope_key: str = DEFAULT_CONFIGURATION_SCOPE,
    ) -> ConfigurationReleaseNode: ...


class InMemoryConfigurationGraphRepository:
    """In-memory test implementation of ConfigurationGraphRepository."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._releases: dict[str, ConfigurationReleaseNode] = {}
        self._domains: dict[tuple[str, str], ConfigurationDomainNode] = {}
        self._active_release_id: str | None = None
        self._head_revisions: dict[str, int] = {DEFAULT_CONFIGURATION_SCOPE: 0}

    async def get_head_revision(self, scope_key: str = DEFAULT_CONFIGURATION_SCOPE) -> int:
        return self._head_revisions.get(scope_key, 0)

    async def get_active_release(self) -> ConfigurationReleaseNode | None:
        if not self._active_release_id:
            return None
        return await self._get_release_copy(self._active_release_id)

    async def get_release(self, release_id: str) -> ConfigurationReleaseNode | None:
        return await self._get_release_copy(release_id)

    async def _get_release_copy(self, release_id: str) -> ConfigurationReleaseNode | None:
        rel = self._releases.get(release_id)
        return copy.deepcopy(rel) if rel else None

    async def list_releases(self, limit: int = 20) -> list[ConfigurationReleaseNode]:
        items = sorted(self._releases.values(), key=lambda r: r.created_at, reverse=True)
        return [copy.deepcopy(r) for r in items[:limit]]

    async def get_domain_config(self, release_id: str, domain_key: str) -> dict[str, Any] | None:
        node = self._domains.get((release_id, domain_key))
        return copy.deepcopy(node.payload) if node else None

    async def get_all_domain_configs(self, release_id: str) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for (rel_id, dk), node in self._domains.items():
            if rel_id == release_id:
                result[dk] = copy.deepcopy(node.payload)
        return result

    async def save_draft_domain(
        self, release_id: str, domain_key: str, payload: dict[str, Any], actor_id: str
    ) -> None:
        async with self._lock:
            if release_id not in self._releases:
                self._releases[release_id] = ConfigurationReleaseNode(
                    release_id=release_id, status="DRAFT", created_by=actor_id
                )
            release = self._releases[release_id]
            if release.status != "DRAFT":
                raise ValueError(
                    f"Cannot edit domain in release {release_id} with status {release.status}"
                )

            existing = self._domains.get((release_id, domain_key))
            version = (existing.version + 1) if existing else 1
            self._domains[(release_id, domain_key)] = ConfigurationDomainNode(
                domain_key=domain_key,
                payload=copy.deepcopy(payload),
                version=version,
                updated_by=actor_id,
            )
            await self._recompute_checksum(release_id)

    async def promote_release(
        self,
        release_id: str,
        status: str,
        actor_id: str,
        *,
        expected_head_revision: int | None = None,
        scope_key: str = DEFAULT_CONFIGURATION_SCOPE,
    ) -> ConfigurationReleaseNode:
        async with self._lock:
            if release_id not in self._releases:
                raise ValueError(f"Release {release_id} not found.")
            current = self._releases[release_id]
            allowed_transitions = {
                "DRAFT": {"VALIDATED", "ARCHIVED"},
                "VALIDATED": {"RELEASED", "ARCHIVED"},
                "SUPERSEDED": {"ARCHIVED"},
            }
            if status not in allowed_transitions.get(current.status, set()):
                raise ValueError(f"Invalid configuration transition {current.status} -> {status}")

            if status == "RELEASED":
                current_revision = self._head_revisions.get(scope_key, 0)
                if expected_head_revision is None:
                    raise ValueError("expected_head_revision is required to publish a release")
                if expected_head_revision != current_revision:
                    raise ConfigurationRevisionConflict(
                        f"Configuration head revision changed from {expected_head_revision} "
                        f"to {current_revision}"
                    )

            await self._recompute_checksum(release_id)
            current = self._releases[release_id]
            updated = current.model_copy(update={"status": status})

            if status == "RELEASED":
                if self._active_release_id and self._active_release_id != release_id:
                    previous = self._releases[self._active_release_id]
                    self._releases[self._active_release_id] = previous.model_copy(
                        update={"status": "SUPERSEDED"}
                    )
                self._active_release_id = release_id
                current_revision = self._head_revisions.get(scope_key, 0)
                self._head_revisions[scope_key] = current_revision + 1

            self._releases[release_id] = updated
            return copy.deepcopy(updated)

    async def _recompute_checksum(self, release_id: str) -> None:
        domain_keys = sorted([k[1] for k in self._domains.keys() if k[0] == release_id])
        hasher = hashlib.sha256()
        for dk in domain_keys:
            node = self._domains[(release_id, dk)]
            hasher.update(dk.encode("utf-8"))
            hasher.update(json.dumps(node.payload, sort_keys=True).encode("utf-8"))
        checksum = hasher.hexdigest()
        old = self._releases[release_id]
        self._releases[release_id] = ConfigurationReleaseNode(
            release_id=old.release_id,
            status=old.status,
            created_at=old.created_at,
            created_by=old.created_by,
            checksum_sha256=checksum,
            metadata=old.metadata,
        )


class Neo4jConfigurationGraphRepository:
    """Neo4j driver implementation of ConfigurationGraphRepository."""

    def __init__(self, driver: AsyncDriver) -> None:
        self._driver = driver

    async def get_head_revision(self, scope_key: str = DEFAULT_CONFIGURATION_SCOPE) -> int:
        query = """
        OPTIONAL MATCH (h:ConfigurationHead {scope_key: $scope_key})
        RETURN coalesce(h.revision, 0) AS revision
        """
        async with self._driver.session() as session:
            result = await session.run(query, scope_key=scope_key)
            record = await result.single()
            return int(record["revision"]) if record else 0

    @staticmethod
    def _decode_object_payload(payload_raw: object, *, context: str) -> dict[str, Any]:
        try:
            decoded = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON in {context}") from exc
        if not isinstance(decoded, dict):
            raise ValueError(f"Configuration payload in {context} must be a JSON object")
        return cast(dict[str, Any], decoded)

    @staticmethod
    def _parse_release_node(record: Mapping[str, Any] | Any) -> ConfigurationReleaseNode:
        created_at_raw = record.get("created_at")
        if isinstance(created_at_raw, datetime):
            created_at = created_at_raw
        elif created_at_raw:
            created_at = datetime.fromisoformat(str(created_at_raw).replace("Z", "+00:00"))
        else:
            created_at = datetime.now(UTC)
        meta_raw = record.get("metadata_json", "{}")
        metadata = Neo4jConfigurationGraphRepository._decode_object_payload(
            meta_raw or "{}",
            context=f"release {record.get('release_id', '')} metadata",
        )
        return ConfigurationReleaseNode(
            release_id=str(record.get("release_id", "")),
            status=str(record.get("status", "DRAFT")),
            created_at=created_at,
            created_by=str(record.get("created_by", "system")),
            checksum_sha256=str(record.get("checksum_sha256", "")),
            metadata=metadata,
        )

    async def get_active_release(self) -> ConfigurationReleaseNode | None:
        query = """
        MATCH (:ConfigurationHead {scope_key: $scope_key})-[:ACTIVE]->(r:ConfigurationRelease)
        RETURN r.release_id AS release_id, r.status AS status,
               r.created_at AS created_at, r.created_by AS created_by,
               r.checksum_sha256 AS checksum_sha256, r.metadata_json AS metadata_json
        LIMIT 1
        """
        async with self._driver.session() as session:
            result = await session.run(query, scope_key=DEFAULT_CONFIGURATION_SCOPE)
            record = await result.single()
            if not record:
                return None
            return self._parse_release_node(record)

    async def get_release(self, release_id: str) -> ConfigurationReleaseNode | None:
        query = """
        MATCH (r:ConfigurationRelease {release_id: $release_id})
        RETURN r.release_id AS release_id, r.status AS status,
               r.created_at AS created_at, r.created_by AS created_by,
               r.checksum_sha256 AS checksum_sha256, r.metadata_json AS metadata_json
        """
        async with self._driver.session() as session:
            result = await session.run(query, release_id=release_id)
            record = await result.single()
            if not record:
                return None
            return self._parse_release_node(record)

    async def list_releases(self, limit: int = 20) -> list[ConfigurationReleaseNode]:
        query = """
        MATCH (r:ConfigurationRelease)
        RETURN r.release_id AS release_id, r.status AS status,
               r.created_at AS created_at, r.created_by AS created_by,
               r.checksum_sha256 AS checksum_sha256, r.metadata_json AS metadata_json
        ORDER BY r.created_at DESC
        LIMIT $limit
        """
        async with self._driver.session() as session:
            result = await session.run(query, limit=limit)
            records = await result.data()
            return [self._parse_release_node(rec) for rec in records]

    async def get_domain_config(self, release_id: str, domain_key: str) -> dict[str, Any] | None:
        query = """
        MATCH (r:ConfigurationRelease {release_id: $release_id})-[:HAS_DOMAIN]->(
            d:ConfigurationDomain {domain_key: $domain_key}
        )
        RETURN d.payload_json AS payload_json
        """
        async with self._driver.session() as session:
            result = await session.run(query, release_id=release_id, domain_key=domain_key)
            record = await result.single()
            if not record:
                return None
            return self._decode_object_payload(
                record["payload_json"],
                context=f"release {release_id} domain {domain_key}",
            )

    async def get_all_domain_configs(self, release_id: str) -> dict[str, Any]:
        query = """
        MATCH (r:ConfigurationRelease {release_id: $release_id})-[:HAS_DOMAIN]->(
            d:ConfigurationDomain
        )
        RETURN d.domain_key AS domain_key, d.payload_json AS payload_json
        """
        result_map: dict[str, Any] = {}
        async with self._driver.session() as session:
            result = await session.run(query, release_id=release_id)
            records = await result.data()
            for rec in records:
                dk = str(rec.get("domain_key", ""))
                result_map[dk] = self._decode_object_payload(
                    rec.get("payload_json", "{}"),
                    context=f"release {release_id} domain {dk}",
                )
        return result_map

    async def save_draft_domain(
        self, release_id: str, domain_key: str, payload: dict[str, Any], actor_id: str
    ) -> None:
        rel = await self.get_release(release_id)
        if rel and rel.status != "DRAFT":
            raise ValueError(f"Cannot edit domain in release {release_id} with status {rel.status}")

        query = """
        MERGE (r:ConfigurationRelease {release_id: $release_id})
        ON CREATE SET r.status = 'DRAFT', r.created_at = datetime(),
                      r.created_by = $actor_id, r.checksum_sha256 = ''
        SET r.status = r.status
        WITH r
        WHERE r.status = 'DRAFT'
        MERGE (r)-[:HAS_DOMAIN]->(d:ConfigurationDomain {
            release_id: $release_id,
            domain_key: $domain_key
        })
        ON CREATE SET d.version = 1, d.created_at = datetime()
        ON MATCH SET d.version = coalesce(d.version, 1) + 1
        SET d.payload_json = $payload_json, d.updated_at = datetime(), d.updated_by = $actor_id
        RETURN r.release_id AS release_id
        """
        payload_json = json.dumps(payload, sort_keys=True)
        async with self._driver.session() as session:
            result = await session.run(
                query,
                release_id=release_id,
                domain_key=domain_key,
                payload_json=payload_json,
                actor_id=actor_id,
            )
            record = await result.single()
            if record is None:
                current = await self.get_release(release_id)
                status = current.status if current is not None else "UNKNOWN"
                raise ValueError(f"Cannot edit domain in release {release_id} with status {status}")
        await self._recompute_checksum(release_id)

    async def promote_release(
        self,
        release_id: str,
        status: str,
        actor_id: str,
        *,
        expected_head_revision: int | None = None,
        scope_key: str = DEFAULT_CONFIGURATION_SCOPE,
    ) -> ConfigurationReleaseNode:
        rel = await self.get_release(release_id)
        if not rel:
            raise ValueError(f"Release {release_id} not found.")
        allowed_transitions = {
            "DRAFT": {"VALIDATED", "ARCHIVED"},
            "VALIDATED": {"RELEASED", "ARCHIVED"},
            "SUPERSEDED": {"ARCHIVED"},
        }
        if status not in allowed_transitions.get(rel.status, set()):
            raise ValueError(f"Invalid configuration transition {rel.status} -> {status}")
        parameters: dict[str, Any]
        if status == "RELEASED":
            await self._recompute_checksum(release_id)
            if expected_head_revision is None:
                raise ValueError("expected_head_revision is required to publish a release")
            query = """
            MATCH (r:ConfigurationRelease {release_id: $release_id})
            WHERE r.status = 'VALIDATED'
            MERGE (h:ConfigurationHead {scope_key: $scope_key})
            ON CREATE SET h.revision = 0, h.created_at = datetime()
            SET h.revision = h.revision
            WITH r, h
            WHERE h.revision = $expected_head_revision
            OPTIONAL MATCH (h)-[active_rel:ACTIVE]->(previous:ConfigurationRelease)
            DELETE active_rel
            FOREACH (_ IN CASE
                WHEN previous IS NULL OR previous.release_id = r.release_id THEN []
                ELSE [1]
            END |
                SET previous.status = 'SUPERSEDED',
                    previous.updated_at = datetime(),
                    previous.updated_by = $actor_id
            )
            MERGE (h)-[:ACTIVE]->(r)
            SET h.revision = h.revision + 1,
                h.updated_at = datetime(),
                h.updated_by = $actor_id,
                r.status = 'RELEASED',
                r.updated_at = datetime(),
                r.updated_by = $actor_id
            RETURN r.release_id AS release_id, r.status AS status,
                   r.created_at AS created_at, r.created_by AS created_by,
                   r.checksum_sha256 AS checksum_sha256,
                   r.metadata_json AS metadata_json
            """
            parameters = {
                "release_id": release_id,
                "scope_key": scope_key,
                "expected_head_revision": expected_head_revision,
                "actor_id": actor_id,
            }
        else:
            query = """
            MATCH (r:ConfigurationRelease {release_id: $release_id})
            WHERE r.status = $expected_status
            SET r.status = $status, r.updated_at = datetime(), r.updated_by = $actor_id
            RETURN r.release_id AS release_id, r.status AS status,
                   r.created_at AS created_at, r.created_by AS created_by,
                   r.checksum_sha256 AS checksum_sha256,
                   r.metadata_json AS metadata_json
            """
            parameters = {
                "release_id": release_id,
                "status": status,
                "expected_status": rel.status,
                "actor_id": actor_id,
            }
        async with self._driver.session() as session:
            result = await session.run(query, **parameters)
            record = await result.single()
            if not record:
                if status == "RELEASED":
                    current_revision = await self.get_head_revision(scope_key)
                    current_release = await self.get_release(release_id)
                    if current_release is not None and current_release.status != "VALIDATED":
                        raise ValueError(f"Release {release_id} is no longer VALIDATED")
                    raise ConfigurationRevisionConflict(
                        f"Configuration head revision changed from "
                        f"{expected_head_revision} to {current_revision}"
                    )
                raise ValueError(f"Release {release_id} could not be updated.")

        if status == "VALIDATED":
            await self._recompute_checksum(release_id)
            refreshed = await self.get_release(release_id)
            if refreshed is None:
                raise ValueError(f"Release {release_id} disappeared after validation.")
            return refreshed
        return self._parse_release_node(record)

    async def _recompute_checksum(self, release_id: str) -> str:
        query = """
        MATCH (r:ConfigurationRelease {release_id: $release_id})-[:HAS_DOMAIN]->(
            d:ConfigurationDomain
        )
        RETURN d.domain_key AS domain_key, d.payload_json AS payload_json
        ORDER BY d.domain_key ASC
        """
        async with self._driver.session() as session:
            result = await session.run(query, release_id=release_id)
            records = await result.data()
            hasher = hashlib.sha256()
            for rec in records:
                dk = str(rec.get("domain_key", ""))
                pj = str(rec.get("payload_json", "{}"))
                hasher.update(dk.encode("utf-8"))
                hasher.update(pj.encode("utf-8"))
            checksum = hasher.hexdigest()
            update_query = """
            MATCH (r:ConfigurationRelease {release_id: $release_id})
            SET r.checksum_sha256 = $checksum
            """
            await session.run(update_query, release_id=release_id, checksum=checksum)
            return checksum
