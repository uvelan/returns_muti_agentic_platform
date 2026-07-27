-- Migration 0010: Constraints for versioned graph-backed configuration.
-- Drop obsolete constraints created by earlier development revisions.

DROP CONSTRAINT uq_configuration_release_checksum IF EXISTS;
DROP CONSTRAINT uq_configuration_domain_key IF EXISTS;

CREATE CONSTRAINT uq_configuration_release_id IF NOT EXISTS
FOR (r:ConfigurationRelease)
REQUIRE r.release_id IS UNIQUE;

CREATE CONSTRAINT uq_configuration_head_scope IF NOT EXISTS
FOR (h:ConfigurationHead)
REQUIRE h.scope_key IS UNIQUE;

CREATE CONSTRAINT uq_configuration_domain_release_key IF NOT EXISTS
FOR (d:ConfigurationDomain)
REQUIRE (d.release_id, d.domain_key) IS UNIQUE;
