"""Small, additive compatibility upgrades for databases from earlier releases.

Run inside init_db's transaction and PostgreSQL advisory lock. Existing messages
never acquire inferred consent: legacy delivery jobs remain unscoped and cancel.
No rows are deleted, relabeled as real, or assigned to a watch automatically.
"""

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection


def apply_additive_upgrades(connection: Connection) -> None:
    timestamp = "TIMESTAMPTZ" if connection.dialect.name == "postgresql" else "DATETIME"
    upgrades = {
        "people": {
            "photon_platform": "VARCHAR",
            "messaging_opted_out_at": timestamp,
        },
        "delivery_jobs": {
            "parent_id": "VARCHAR",
            "visibility": "VARCHAR NOT NULL DEFAULT 'unscoped'",
            "recipient_role": "VARCHAR",
            "account_required": "BOOLEAN NOT NULL DEFAULT FALSE",
            "account_created_at": timestamp,
            "family_link_id": "INTEGER",
            "care_circle_id": "INTEGER",
            "request_id": "VARCHAR",
            "offer_only": "BOOLEAN NOT NULL DEFAULT FALSE",
            "require_share_health": "BOOLEAN NOT NULL DEFAULT FALSE",
            "require_share_checkins": "BOOLEAN NOT NULL DEFAULT FALSE",
            "require_emergency": "BOOLEAN NOT NULL DEFAULT FALSE",
        },
    }
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    for table, columns in upgrades.items():
        if table not in tables:
            continue
        existing = {column["name"] for column in inspector.get_columns(table)}
        for name, declaration in columns.items():
            if name not in existing:
                # All identifiers and declarations are static constants above.
                connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}"))
    if "delivery_jobs" in tables:
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_delivery_jobs_parent_id ON delivery_jobs(parent_id)"
            )
        )
