"""Shared-contract checks: fixtures validate, and exported schemas match the code."""

import json
from pathlib import Path

import pytest

from app.schemas.contracts import EXPORTED_CONTRACTS

CONTRACTS = Path(__file__).resolve().parents[2] / "packages" / "contracts"
FIXTURES = sorted((CONTRACTS / "fixtures").glob("*.json"))


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_fixture_matches_contract(path):
    model = EXPORTED_CONTRACTS[path.stem]
    model.model_validate(json.loads(path.read_text()))


@pytest.mark.parametrize("name", sorted(EXPORTED_CONTRACTS))
def test_exported_schema_is_current(name):
    committed = CONTRACTS / "schemas" / f"{name}.schema.json"
    assert committed.exists(), "Run: cd backend && uv run python scripts/export_contracts.py"
    current = EXPORTED_CONTRACTS[name].model_json_schema()
    assert json.loads(committed.read_text()) == current, (
        f"{name} contract changed without re-exporting. Run "
        "`cd backend && uv run python scripts/export_contracts.py` and follow the contract "
        "change process in AGENTS.md."
    )


def test_fall_confidence_is_labeled_as_model_output():
    schema = EXPORTED_CONTRACTS["fall_suspected_event"].model_json_schema()
    assert (
        "NOT a validated clinical probability" in schema["properties"]["confidence"]["description"]
    )
