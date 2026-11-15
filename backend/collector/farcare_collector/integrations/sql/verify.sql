SELECT source,metric,unit,count(*) AS current_measurements,
       min(observed_at) AS first_time,max(observed_at) AS latest_time
FROM air_health.measurements_current GROUP BY source,metric,unit ORDER BY source,metric;
SELECT count(*) AS stored_versions,
       count(DISTINCT event_id) AS distinct_events
FROM air_health.measurement_versions;
SELECT chunk_schema,chunk_name,compression_status,
       before_compression_total_bytes,after_compression_total_bytes,
       round(100.0 * (1.0 - after_compression_total_bytes::numeric /
             NULLIF(before_compression_total_bytes,0)),2) AS measured_saving_percent
FROM chunk_columnstore_stats('air_health.measurement_versions');
