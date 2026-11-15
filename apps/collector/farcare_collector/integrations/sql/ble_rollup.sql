-- BLE notification identities are immutable in the collector. Cloud revisions
-- deliberately do not enter this aggregate; use measurements_current for them.
CREATE MATERIALIZED VIEW IF NOT EXISTS air_health.ble_hr_1m
WITH (timescaledb.continuous, timescaledb.materialized_only=false) AS
SELECT time_bucket(INTERVAL '1 minute',observed_at) AS bucket,
       subject_id,source_stream,count(*) AS samples,
       avg(value) AS mean_bpm,min(value) AS min_bpm,max(value) AS max_bpm,
       min(observed_at) AS first_seen,max(observed_at) AS last_seen
FROM air_health.measurement_versions
WHERE source='ble' AND metric='heart_rate_bpm' AND canonical_eligible
GROUP BY 1,2,3
WITH NO DATA;
SELECT add_continuous_aggregate_policy('air_health.ble_hr_1m',
    start_offset=>INTERVAL '2 days',end_offset=>INTERVAL '1 minute',
    schedule_interval=>INTERVAL '1 minute',if_not_exists=>true);
-- Missing buckets remain absent. samples counts received notifications; it is
-- not a wear-time percentage or proof of activity/inactivity.
