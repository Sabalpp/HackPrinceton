"""Check the Tiger (live watch data) connection. Prints no credentials and no health values.

    uv run python scripts/check_tiger.py

Uses TIGER_DATABASE_URL from backend/.env (or DATABASE_URL when it is blank) and reports:
the connection, that it is read-only, that the collector's air_health tables exist, which
watch wearers (subject ids) have recent records, and which FarCare parents are linked.
"""

import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402
from sqlmodel import Session  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import get_engine, tiger_engine  # noqa: E402


def masked(url: str) -> str:
    u = urlparse(url)
    host = u.hostname or "?"
    return f"{u.scheme}://…{host[-24:]}:{u.port or ''}{u.path}"


def main() -> int:
    s = get_settings()
    separate = bool(s.tiger_database_url.strip())
    engine = tiger_engine() if separate else get_engine()
    url = s.tiger_database_url if separate else s.database_url
    print(
        f"Watch data source: {'TIGER_DATABASE_URL (separate, read-only)' if separate else 'DATABASE_URL'}"
    )
    print(f"  {masked(url)}")
    if engine.dialect.name != "postgresql":
        print("  ✗ Not PostgreSQL. Paste the Tiger connection string into TIGER_DATABASE_URL.")
        return 1
    try:
        with Session(engine) as db:
            db.execute(text("SET LOCAL statement_timeout = '8s'"))
            version = db.execute(text("SELECT version()")).scalar() or ""
            print(f"  ✓ connected ({version.split(',')[0]})")
            read_only = db.execute(text("SHOW transaction_read_only")).scalar()
            print(f"  {'✓' if read_only == 'on' else '•'} read-only session: {read_only}")
            tables = [
                r[0]
                for r in db.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'air_health' "
                        "ORDER BY 1"
                    )
                ).all()
            ]
            views = [
                r[0]
                for r in db.execute(
                    text(
                        "SELECT table_name FROM information_schema.views WHERE table_schema = 'air_health' "
                        "ORDER BY 1"
                    )
                ).all()
            ]
            if "measurement_versions" not in tables:
                print(
                    "  ✗ air_health.measurement_versions not found. Is this the collector's database?"
                )
                return 1
            print(
                f"  ✓ air_health tables: {', '.join(tables)}"
                + (f" | views: {', '.join(views)}" if views else "")
            )
            rows = (
                db.execute(
                    text("""
                SELECT subject_id,
                       string_agg(DISTINCT source, ',') AS sources,
                       max(observed_at) AS last_seen,
                       count(*) FILTER (WHERE observed_at > now() - interval '24 hours') AS last_24h
                FROM air_health.measurement_versions
                WHERE canonical_eligible
                GROUP BY subject_id ORDER BY max(observed_at) DESC LIMIT 10
            """)
                )
                .mappings()
                .all()
            )
            print("Watch wearers with eligible records (newest first):")
            for r in rows:
                print(
                    f"  subject_id={r['subject_id']}  sources={r['sources']}  "
                    f"last_seen={r['last_seen']:%Y-%m-%d %H:%M:%S %Z}  records_24h={r['last_24h']}"
                )
            if not rows:
                print("  (none yet: is the collector running near the watch?)")
    except Exception as exc:  # report the failure class without dumping connection details
        print(f"  ✗ {type(exc).__name__}: {str(exc).splitlines()[0][:160]}")
        return 1

    with Session(get_engine()) as app_db:
        try:
            links = app_db.execute(
                text("SELECT parent_id, subject_id, enabled, sync_status FROM watch_source_links")
            ).all()
        except Exception:
            links = []
    print("FarCare parents linked to a watch:")
    for parent_id, subject_id, enabled, status in links:
        print(f"  {parent_id} -> {subject_id} enabled={enabled} daily_sync={status}")
    if not links:
        print("  (none: link one with python -m app.health.sources link …)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
