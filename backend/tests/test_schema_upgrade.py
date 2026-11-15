"""Older pilot databases upgrade without dropping records or inferring consent."""

from sqlalchemy import inspect, text
from sqlmodel import Session, create_engine

from app import db
from app.models import Person
from app.schema_upgrades import apply_additive_upgrades


def test_old_people_and_delivery_rows_survive_idempotent_upgrade(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'previous-release.db'}")
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE people (id VARCHAR PRIMARY KEY, display_name VARCHAR)")
        )
        connection.execute(text("INSERT INTO people VALUES ('synthetic-parent', 'Test parent')"))
        connection.execute(
            text("CREATE TABLE delivery_jobs (id VARCHAR PRIMARY KEY, text VARCHAR)")
        )
        connection.execute(
            text("INSERT INTO delivery_jobs VALUES ('old-job', 'Synthetic message')")
        )
        connection.execute(
            text("CREATE TABLE outbound_messages (id VARCHAR PRIMARY KEY, text VARCHAR)")
        )
        connection.execute(
            text("INSERT INTO outbound_messages VALUES ('old-out', 'Existing message')")
        )
        apply_additive_upgrades(connection)
        apply_additive_upgrades(connection)
        person = connection.execute(text("SELECT * FROM people")).mappings().one()
        assert person["display_name"] == "Test parent"
        assert person["photon_platform"] is None
        assert person["messaging_opted_out_at"] is None
        job = connection.execute(text("SELECT * FROM delivery_jobs")).mappings().one()
        assert job["text"] == "Synthetic message"
        assert job["visibility"] == "unscoped"
        assert job["parent_id"] is None
        assert not job["require_share_health"]
        outbound = connection.execute(text("SELECT * FROM outbound_messages")).mappings().one()
        assert outbound["text"] == "Existing message"
        assert outbound["access_contexts"] is None  # no inferred legacy sharing grant
        indexes = inspect(connection).get_indexes("delivery_jobs")
        assert sum(i["name"] == "ix_delivery_jobs_parent_id" for i in indexes) == 1


def test_startup_upgrades_an_existing_person_without_recreating_it(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'upgrade-startup.db'}")
    monkeypatch.setattr(db, "_engine", engine)
    try:
        db.init_db()
        with Session(engine) as session:
            session.add(Person(id="existing-person", role="parent", display_name="Test wearer"))
            session.commit()
        # Model a real previous installation, where these columns did not exist.
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE people DROP COLUMN photon_platform"))
            connection.execute(text("ALTER TABLE people DROP COLUMN messaging_opted_out_at"))
        db.init_db()
        db.init_db()
        with Session(engine) as session:
            person = session.get(Person, "existing-person")
            assert person is not None and person.display_name == "Test wearer"
            assert person.photon_platform is None and person.messaging_opted_out_at is None
    finally:
        engine.dispose()
