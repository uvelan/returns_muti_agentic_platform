// Backfill optional release metadata without assuming the property key exists.
MATCH (r:ConfigurationRelease)
WHERE properties(r)['metadata_json'] IS NULL
SET r.metadata_json = '{}';
