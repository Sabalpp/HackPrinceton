"""TIGER_DATABASE_URL: an optional, separate, READ-ONLY source of live watch data."""

import os

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app import db
from app.config import get_settings


def test_without_tiger_url_watch_reads_use_the_app_database(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "tiger_database_url", "")
    db.reset_engine()
    with db.session_scope() as main, db.watch_data_session(main) as watch:
        assert watch is main  # single-database deployments behave exactly as before
    assert db.tiger_engine() is None


@pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL", "").startswith("postgres"),
    reason="needs a PostgreSQL TEST_DATABASE_URL (CI provides one)",
)
def test_tiger_session_is_separate_and_read_only(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "tiger_database_url", os.environ["TEST_DATABASE_URL"])
    db.reset_engine()
    try:
        with db.session_scope() as main, db.watch_data_session(main) as watch:
            assert watch is not main
            assert watch.execute(text("SHOW transaction_read_only")).scalar() == "on"
            with pytest.raises(DBAPIError):
                watch.execute(text("CREATE TABLE farcare_should_not_exist (id int)"))
    finally:
        monkeypatch.setattr(get_settings(), "tiger_database_url", "")
        db.reset_engine()


def test_live_panel_reports_unavailable_not_fake_data_when_tiger_is_unreachable(
    client, monkeypatch
):
    """A broken Tiger connection must never surface stale readings as current."""
    from app.health.live import read_sensor_health
    from app.health.live_models import WatchSourceLink
    from app.models import Person

    monkeypatch.setattr(
        get_settings(), "tiger_database_url", "postgresql://u:p@127.0.0.1:9/nothing_here"
    )
    db.reset_engine()
    try:
        with db.session_scope() as s:
            s.add(WatchSourceLink(parent_id="parent_001", subject_id="subject-test"))
            s.flush()
            report = read_sensor_health(s, s.get(Person, "parent_001"))
        assert report.connection_status == "unavailable"
        assert report.recent_heart_rate == []  # no stale points shown as current
    finally:
        monkeypatch.setattr(get_settings(), "tiger_database_url", "")
        db.reset_engine()
