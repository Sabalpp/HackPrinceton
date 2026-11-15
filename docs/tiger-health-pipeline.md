# Fitbit and Tiger health data in FarCare

FarCare reads the collector's real, canonical sensor records from Tiger. The app
does not generate replacement measurements when a watch, Google sync, or database
is unavailable.

```text
Fitbit Air -- BLE notifications --> Mac collector --> Tiger measurement_versions
Google Health -- authorized sync ------------------> Tiger measurement_versions
                                                         |
                          explicit wearer/parent link ---+
                                                         |
                  +--------------------------------------+------------------+
                  |                                                         |
        authenticated sensor-health API                         durable worker (every minute)
                  |                                                         |
        parent / permitted family UI                           dated daily health observations
        latest values + 300-point HR history                   + existing personal trend rules
                                                                            |
                                                               permission-checked message queue
                                                                            |
                                                               Photon SMS + reply processing
```

The BLE collector still runs on a computer physically near the watch. Cloudflare
cannot directly receive the watch's radio signal. The collector writes individual
observations; the dashboard reads current records, without waiting for the
one-minute `ble_hr_1m` aggregate to refresh. Google metrics appear only after a
successful authorized Google data pull and collector import.

Run one `health_pipeline watch` process per local outbox. When switching from a
managed-session collector to a Terminal collector, stop the old ingester first;
keep the separate BLE reader running. Restarting the pipeline resumes its durable
outbox and delivery checkpoints, so recorded samples can catch up after an outage.
An acknowledged Tiger upload is already committed even if a later local status
write fails. Status and report snapshots use private, unique temporary files in
the destination directory before atomic replacement, so overlapping writers cannot
remove each other's temporary file. A healthy uploader does not imply that the
band is connected: check the BLE timestamp as well as the delivery backlog.

## Explicit account and source registration

The API and worker must use the same PostgreSQL `DATABASE_URL` as the collector's
`air_health` schema. Keep database credentials in the deployment secret store.
No connection string or health history belongs in the web bundle or Git.

Create the real family account and parent care profile in FarCare first. Then an
operator runs, from `backend` using the deployment environment:

```sh
.venv/bin/python -m app.health.sources link \
  --parent-id REAL_PARENT_ID \
  --actor-id REGISTERED_ACCOUNT_OWNER_ID \
  --subject-id COLLECTOR_WEARER_SUBJECT_ID \
  --consent-confirmed
```

`--consent-confirmed` records that the wearer authorized this use of their data.
The actor must be the registered parent or the registered account that created
their care profile. The command verifies existing eligible Tiger records and
creates an audited `watch_source_links` mapping. It rejects demo seed profiles,
unknown accounts, reassignment to another wearer, and a subject already linked to
a different parent. Repeating the same active link is safe. A browser never
supplies an upstream subject ID.

To pause app access and worker synchronization without deleting measurements:

```sh
.venv/bin/python -m app.health.sources disable \
  --parent-id REAL_PARENT_ID --actor-id REGISTERED_ACCOUNT_OWNER_ID
```

Disabling a source leaves previous dated daily observations in the account.
Removing historical data is a separate deliberate account/data-retention action.

## Dashboard contract

`GET /api/parents/{parent_id}/sensor-health` requires authenticated parent access
or an authorized family membership with `share_health`. Nearby responders cannot
read this endpoint, even if a malformed family membership exists. With sharing
off it returns `null`, consistent with the existing health endpoints.

The response provides:

- Connection state: `not_linked`, `available`, or `unavailable`. `available` means
  the database query succeeded; it does not mean the watch is currently connected.
- Live BLE heart rate: source, exact value, unit, timestamp, age and freshness.
- Reported Google metrics: heart rate, resting HR, steps in the latest interval,
  sleep in the latest session, SpO2, HRV, respiratory rate and skin temperature,
  where those records actually exist.
- At most 300 recent HR points within 15 minutes, newest watch stream only. This
  is a bounded preview, not guaranteed complete 15-minute coverage. It never
  interpolates gaps or silently combines simultaneous watches.
- Last successful daily-sync time and pending/ok/unavailable worker status.

Freshness is an engineering availability policy, not a clinical threshold: live
BLE samples become stale after 20 seconds; cloud samples and intervals use
metric-specific windows; daily metrics use their source calendar date and become
stale after two days. Stale values stay dated. Missing or invalid values stay
`null`; no absent value turns into zero. Genuine reported zero steps are retained.

Instantaneous BPM is never called resting HR and is never used to infer HRV,
blood pressure, SpO2, a fall or a raw waveform. Cloud steps are explicitly an
interval value in this endpoint, not an invented daily total. Daily summaries use
the existing stricter projection, excluding today's incomplete step totals and
ambiguous overlapping streams.

## Daily synchronization and messages

Run the existing durable worker: `python -m app.worker`. Its singleton maintenance
transaction calls `sync_linked_sources`. Each cycle checks at most one due link;
each link is retried no sooner than 60 seconds later. The worker projects up to
35 dates of existing Google steps, declared resting HR and main sleep. Stable
observation IDs and a digest prevent duplicate history, timeline events and
messages. Source corrections can clear a previously stored value; null-only dates
are not inserted as evidence that data was collected.

The existing descriptive trend rules compare five recent dates with at least
seven baseline dates. Their relative-change defaults are product heuristics,
not medically validated danger thresholds. A new, recent live daily change can
record one family check-in prompt per parent, recipient and local day. The text
asks the family to check in and labels itself as a wellbeing prompt. It does not
declare an emergency or diagnose a condition. The rules do not alert from a
short, stale, simulated or missing history.

Only family members with health sharing receive the health notice. The normal
outbound gateway enforces permissions and opt-out checks again at delivery. The
outbox records `queued`, `available`, failure and provider delivery states
accurately; recording a notice does not mean an SMS was delivered. Actual SMS
requires configured Photon credentials, a working number/route, the worker and
an authenticated inbound webhook. Registered users without a ready Photon route
have the notice available in the app.

## Query cost and failures

Live queries use indexed, bounded candidate lookups against
`air_health.measurement_versions` and resolve the newest eligible revision per
event by `source_revision_at`, `received_at`, then `version_hash`. This has the
same identity/revision rule as `measurements_current`, avoiding a full canonical
view sort for every dashboard refresh. Candidate limits are 600 BLE versions and
20 versions per cloud metric. Sparse older measurements beyond the 7-day BLE or
35-day cloud search windows are reported missing, not silently substituted.

The collector schema already defines these useful indexes:

```sql
-- Existing collector indexes; do not recreate them on every app startup.
-- measurement_versions_lookup_idx:
--   (subject_id, metric, observed_at DESC)
-- measurement_versions_identity_idx:
--   (event_id, source_revision_at DESC, received_at DESC)
```

Use `EXPLAIN (ANALYZE, BUFFERS)` against a safe bounded read to evaluate additional
indexing with actual service size. If the plan scans many BLE rows while looking
for a Google heart-rate record, consider an operator-managed index on
`(subject_id, source, metric, observed_at DESC)` restricted to eligible versions;
the extra `source` key separates those two streams. Schedule any index creation
through the collector's database migrations. This change does not alter retention or
compression settings and does not create continuous aggregates at request time.

Live reads have a 3-second PostgreSQL statement limit; daily reads have a
5-second limit. Savepoints contain missing-table/statement errors so normal
messages can proceed. A lost database connection propagates to the worker's
transaction boundary for rollback/reconnection. No raw driver error, secret,
subject identifier or health record is included in operational warnings.

## Verification

From `backend`:

```sh
.venv/bin/python -m pytest tests/test_health_live.py tests/test_health_tiger.py -q
.venv/bin/ruff check app/health app/routes/personal_health.py app/schemas/experience.py tests/test_health_live.py
```

Fixtures cover identity isolation, authentication, revoked sharing, neighbor
privacy, invalid values, stale signals, duplicate/conflicting readings, consent,
idempotent updates, null corrections, local calendar dates, message deduplication
and transaction recovery. A PostgreSQL-only test runs the exact production
LATERAL query against synthetic version rows in the disposable CI PostgreSQL
service. It skips on SQLite. **Never point `TEST_DATABASE_URL` at a production
database:** the existing test harness resets application tables.

Deployment still requires an actual collector connection and an explicit real
wearer link. Passing fixture tests is not evidence that the live watch, Tiger
credentials or Photon provider are connected.
