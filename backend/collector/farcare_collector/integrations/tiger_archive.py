"""Lossless delivery of the local health-source archive to PostgreSQL.

Only rows already ingested into Store.raw_records are inputs. No source files,
token files, local paths, or credentials are copied into archive records.
"""

import json

from farcare_collector.integrations.tiger_sink import connect, target_key
from farcare_collector.paths import SQL_ROOT

INSERT_RECORDS = """INSERT INTO air_health.source_records
    (raw_hash,source,data_type,payload,content_type,first_seen)
    VALUES (%s,%s,%s,%s,%s,%s)
    ON CONFLICT (raw_hash) DO NOTHING"""


def _content_type(data_type, payload):
    """Label the stored representation without rewriting or parsing XML."""
    if data_type == "exercise-tcx":
        return "application/xml"

    def invalid_constant(value):
        raise ValueError("Non-JSON numeric constant")

    try:
        json.loads(payload, parse_constant=invalid_constant)
    except (UnicodeError, ValueError, RecursionError):
        return "application/octet-stream"
    return "application/json"


def apply_archive_schema(path):
    """Create the append-only archive table using the existing TLS connection."""
    schema = SQL_ROOT / "archive.sql"
    with connect(path) as connection:
        connection.execute(schema.read_text())


def deliver_archive(store, path, batch_size=1000):
    """Acknowledge raw-record batches only after the remote commit succeeds.

    ``path`` is the same private PostgreSQL credentials file accepted by
    tiger_sink. The separate ``target_key(path) + ':raw'`` checkpoint contains
    SQLite's raw_records rowid; raw_records must remain an append-only outbox.
    Replaying a remote commit after a local checkpoint failure is safe because
    raw_hash is the remote primary key. acknowledged_records counts rows whose
    remote transaction succeeded, including already-present rows during replay.
    Payload bytes are copied exactly as stored; they are never JSON-serialized.
    """
    if type(batch_size) is not int or batch_size <= 0:
        raise ValueError("batch_size must be a positive integer")
    key = target_key(path) + ":raw"
    row = store.db.execute("SELECT last_seq FROM deliveries WHERE target=?", (key,)).fetchone()
    last = row[0] if row else 0
    acknowledged = 0
    with connect(path) as connection:
        while True:
            # Explicit columns ensure source_file is never sent to the server.
            rows = store.db.execute(
                """SELECT rowid,raw_hash,source,data_type,payload,first_seen
                FROM raw_records WHERE rowid>? ORDER BY rowid LIMIT ?""",
                (last, batch_size),
            ).fetchall()
            if not rows:
                break
            values = []
            for rowid, raw_hash, source, data_type, payload, first_seen in rows:
                if not isinstance(payload, (bytes, bytearray, memoryview)):
                    raise ValueError("raw_records payload must be stored as bytes")
                payload = bytes(payload)
                values.append(
                    (
                        raw_hash,
                        source,
                        data_type,
                        payload,
                        _content_type(data_type, payload),
                        first_seen,
                    )
                )
            with connection.cursor() as cursor:
                cursor.executemany(INSERT_RECORDS, values)
            connection.commit()
            last = rows[-1][0]
            with store.db:
                store.db.execute(
                    "INSERT INTO deliveries(target,last_seq) VALUES (?,?) "
                    "ON CONFLICT(target) DO UPDATE SET "
                    "last_seq=max(deliveries.last_seq,excluded.last_seq)",
                    (key, last),
                )
            acknowledged += len(rows)
    return {"acknowledged_records": acknowledged, "last_rowid": last}
