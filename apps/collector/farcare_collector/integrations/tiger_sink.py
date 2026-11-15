"""TLS PostgreSQL delivery with private credentials and an acknowledged outbox."""

import hashlib
import json
import shlex
from pathlib import Path

from farcare_collector.paths import SQL_ROOT


def credentials(path):
    values = {}
    for line in Path(path).expanduser().read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:]
        if "=" not in line:
            raise ValueError("Malformed credentials entry.")
        name, raw = line.split("=", 1)
        if name.strip() not in {
            "TIMESCALE_SERVICE_URL",
            "DATABASE_URL",
            "PGHOST",
            "PGPORT",
            "PGUSER",
            "PGPASSWORD",
            "PGDATABASE",
            "PGSSLMODE",
        }:
            continue
        parts = shlex.split(raw, comments=False)
        if len(parts) != 1:
            raise ValueError("Credentials entries must contain one quoted or unquoted value.")
        values[name.strip()] = parts[0]
    if all(values.get(k) for k in ("PGHOST", "PGUSER", "PGPASSWORD", "PGDATABASE")):
        config = {
            k: values[v]
            for k, v in {
                "host": "PGHOST",
                "user": "PGUSER",
                "password": "PGPASSWORD",
                "dbname": "PGDATABASE",
            }.items()
        }
        config.update(
            port=int(values.get("PGPORT", 5432)), sslmode=values.get("PGSSLMODE", "require")
        )
    else:
        from psycopg.conninfo import conninfo_to_dict

        config = conninfo_to_dict(
            values.get("TIMESCALE_SERVICE_URL") or values.get("DATABASE_URL") or ""
        )
        if not config.get("host") or not config.get("dbname"):
            raise ValueError("No complete PostgreSQL destination found in credentials file.")
        config.setdefault("sslmode", "require")
    if config["sslmode"] not in ("require", "verify-ca", "verify-full"):
        raise ValueError("TLS is required for the health-data destination.")
    config["connect_timeout"] = 10
    config["application_name"] = "air_health_pipeline"
    return config


def connect(path):
    import psycopg

    return psycopg.connect(**credentials(path))


def target_key(path):
    config = credentials(path)
    identity = [
        str(config.get(k, 5432 if k == "port" else "")) for k in ("host", "port", "dbname", "user")
    ]
    return hashlib.sha256("|".join(identity).encode()).hexdigest()


def inspect(path):
    with connect(path) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT current_database(), current_user, current_setting('server_version'), "
                "(SELECT extversion FROM pg_extension WHERE extname='timescaledb'), "
                "(SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid())"
            )
            database, role, version, timescale, tls = cursor.fetchone()
            cursor.execute(
                "SELECT table_schema, table_name FROM information_schema.tables "
                "WHERE table_schema NOT IN ('pg_catalog','information_schema') "
                "AND table_schema NOT LIKE '\\_timescaledb%' ESCAPE '\\' "
                "ORDER BY 1,2 LIMIT 40"
            )
            tables = cursor.fetchall()
    return {
        "database": database,
        "role": role,
        "postgres_version": version,
        "timescaledb_version": timescale,
        "tls": tls,
        "tables": tables,
    }


def apply_schema(path):
    schema = SQL_ROOT / "schema.sql"
    with connect(path) as connection:
        connection.execute(schema.read_text())


def deliver(store, path, batch_size=1000):
    """Advance the local cursor only after the remote transaction commits.

    Replaying a committed batch after a local crash is safe: the remote primary
    key makes each measurement version idempotent. No raw account JSON is sent.
    """
    key = target_key(path)
    row = store.db.execute("SELECT last_seq FROM deliveries WHERE target=?", (key,)).fetchone()
    last = row[0] if row else 0
    delivered = 0
    with connect(path) as connection:
        while True:
            rows = store.db.execute(
                "SELECT seq,payload FROM measurement_versions WHERE seq>? ORDER BY seq LIMIT ?",
                (last, batch_size),
            ).fetchall()
            if not rows:
                break
            values = []
            for seq, payload in rows:
                p = json.loads(payload)
                values.append(
                    (
                        p["observed_at"],
                        p["event_id"],
                        p["version_hash"],
                        p["subject_id"],
                        p["source"],
                        p["source_stream"],
                        p["metric"],
                        p["ended_at"],
                        p["local_date"],
                        p["value"],
                        p["unit"],
                        p["kind"],
                        json.dumps(p["attributes"], allow_nan=False),
                        p["source_revision_at"],
                        p["received_at"],
                        p.get("canonical_eligible", True),
                    )
                )
            with connection.cursor() as cursor:
                cursor.executemany(
                    """INSERT INTO air_health.measurement_versions
                    (observed_at,event_id,version_hash,subject_id,source,source_stream,metric,ended_at,
                     local_date,value,unit,kind,attributes,source_revision_at,received_at,canonical_eligible)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s)
                    ON CONFLICT (observed_at,event_id,version_hash) DO NOTHING""",
                    values,
                )
            connection.commit()
            last = rows[-1][0]
            with store.db:
                store.db.execute(
                    "INSERT INTO deliveries(target,last_seq) VALUES (?,?) "
                    "ON CONFLICT(target) DO UPDATE SET last_seq=max(deliveries.last_seq,excluded.last_seq)",
                    (key, last),
                )
            delivered += len(rows)
    return {"acknowledged_versions": delivered, "last_seq": last}
