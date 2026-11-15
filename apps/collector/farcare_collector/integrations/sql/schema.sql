-- Requires TimescaleDB >=2.20. No existing tables are dropped or altered.
-- Version history is append-only. The current view resolves source revisions.
CREATE SCHEMA IF NOT EXISTS air_health;
CREATE TABLE IF NOT EXISTS air_health.measurement_versions (
    observed_at timestamptz NOT NULL,
    event_id text NOT NULL,
    version_hash text NOT NULL,
    subject_id text NOT NULL,
    source text NOT NULL CHECK (source IN ('ble','google_health')),
    source_stream text NOT NULL,
    metric text NOT NULL,
    ended_at timestamptz,
    local_date date NOT NULL,
    value double precision NOT NULL CHECK (value > '-Infinity'::float8 AND value < 'Infinity'::float8),
    unit text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('sample','interval','daily','session')),
    attributes jsonb NOT NULL,
    source_revision_at timestamptz NOT NULL,
    received_at timestamptz NOT NULL,
    canonical_eligible boolean NOT NULL,
    PRIMARY KEY (observed_at,event_id,version_hash),
    CHECK (ended_at IS NULL OR ended_at >= observed_at)
) WITH (
    tsdb.hypertable,
    tsdb.partition_column='observed_at',
    tsdb.chunk_interval='7 days',
    tsdb.segmentby='subject_id,metric,source_stream',
    tsdb.orderby='observed_at DESC'
);
CREATE INDEX IF NOT EXISTS measurement_versions_identity_idx
    ON air_health.measurement_versions(event_id,source_revision_at DESC,received_at DESC);
CREATE INDEX IF NOT EXISTS measurement_versions_lookup_idx
    ON air_health.measurement_versions(subject_id,metric,observed_at DESC);
CREATE OR REPLACE VIEW air_health.measurements_current AS
SELECT DISTINCT ON (event_id) * FROM air_health.measurement_versions
WHERE canonical_eligible
ORDER BY event_id,source_revision_at DESC,received_at DESC,version_hash DESC;
-- Compression is lossless; no retention/deletion policy is installed.
-- The CREATE TABLE columnstore policy uses the 7-day chunk interval on Tiger Cloud.
