-- Original health-source payloads; ordinary PostgreSQL table, no retention.
-- BLOBs remain byte-for-byte intact, including malformed records and TCX XML.
-- Local source paths and credentials are intentionally absent from the schema.
CREATE SCHEMA IF NOT EXISTS air_health;
CREATE TABLE IF NOT EXISTS air_health.source_records (
    raw_hash text PRIMARY KEY,
    source text NOT NULL CHECK (source IN ('ble','google_health')),
    data_type text NOT NULL,
    payload bytea NOT NULL,
    content_type text NOT NULL,
    first_seen timestamptz NOT NULL
);
