-- Migration 0011: indexes for graph-backed configuration lookup and audit.

CREATE INDEX idx_configuration_release_status IF NOT EXISTS
FOR (r:ConfigurationRelease)
ON (r.status);

CREATE INDEX idx_configuration_release_created_at IF NOT EXISTS
FOR (r:ConfigurationRelease)
ON (r.created_at);

CREATE INDEX idx_configuration_release_checksum IF NOT EXISTS
FOR (r:ConfigurationRelease)
ON (r.checksum_sha256);

CREATE INDEX idx_configuration_domain_updated_at IF NOT EXISTS
FOR (d:ConfigurationDomain)
ON (d.updated_at);
