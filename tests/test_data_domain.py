"""The allowed values (#11): `socboard/domain.py` against `openapi.yaml`, and the CHECK text."""

import pytest
from backend_support import load_contract
from data_support import isolated_database_env, no_default_database_file  # noqa: F401

from socboard import domain
from socboard.models import metadata

ENUM_NAMES = ["Role", "Status", "ClosingReason", "Severity", "AssetCriticality", "AuditAction"]


def test_domain_names_the_six_enums():
    assert sorted(domain.ENUMS) == sorted(ENUM_NAMES)


@pytest.mark.parametrize("name", ENUM_NAMES)
def test_domain_values_equal_the_contract_enum(name):
    contract = load_contract()
    expected = contract["components"]["schemas"][name]["enum"]
    assert sorted(domain.ENUMS[name]) == sorted(expected)
    assert len(set(domain.ENUMS[name])) == len(domain.ENUMS[name])  # no duplicates


def test_the_comparison_notices_a_changed_list():
    contract = load_contract()
    expected = contract["components"]["schemas"]["Status"]["enum"]
    assert sorted(domain.STATUSES + ("Archived",)) != sorted(expected)


def test_in_check_text():
    assert domain.in_check("role", domain.ROLES) == "role IN ('analyst', 'lead')"


@pytest.mark.parametrize("bad", ["", "a'b", "a;b", "a b", "\u00e9", "a-b"])
def test_sql_literal_refuses_anything_but_letters_and_underscores(bad):
    with pytest.raises(ValueError):
        domain.sql_literal(bad)


def test_every_table_and_name_follows_the_design():
    assert sorted(metadata.tables) == [
        "alerts",
        "assets",
        "audit_entries",
        "cases",
        "notes",
        "users",
    ]
    columns = {name: sorted(table.columns.keys()) for name, table in metadata.tables.items()}
    assert columns["users"] == ["display_name", "id", "password_hash", "role", "username"]
    assert columns["assets"] == ["criticality", "id", "name"]
    assert columns["alerts"] == sorted(
        [
            "id",
            "source",
            "alert_type",
            "asset",
            "asset_criticality",
            "title",
            "description",
            "alert_timestamp",
            "ingested_at",
            "severity",
            "severity_rule",
        ]
    )
    assert columns["cases"] == ["alert_id", "closing_reason", "id", "owner_id", "status"]
    assert columns["notes"] == ["author_id", "case_id", "created_at", "id", "text"]
    assert columns["audit_entries"] == ["action", "actor_id", "at", "case_id", "detail", "id"]


def test_every_constraint_and_index_has_a_name_from_the_convention():
    for table in metadata.tables.values():
        for constraint in table.constraints:
            assert constraint.name, f"unnamed constraint on {table.name}"
        for index in table.indexes:
            assert index.name, f"unnamed index on {table.name}"
    assert metadata.naming_convention  # one convention on the metadata


def test_every_key_is_a_64_bit_integer_with_the_sqlite_variant():
    from sqlalchemy import BigInteger, Integer
    from sqlalchemy.dialects import postgresql, sqlite

    for table in metadata.tables.values():
        key_columns = [column for column in table.columns if column.primary_key]
        key_columns += [column for column in table.columns if column.foreign_keys]
        assert key_columns
        for column in key_columns:
            postgres_type = column.type.dialect_impl(postgresql.dialect())
            sqlite_type = column.type.dialect_impl(sqlite.dialect())
            assert isinstance(postgres_type, BigInteger), (table.name, column.name)
            assert isinstance(sqlite_type, Integer), (table.name, column.name)
            assert not isinstance(sqlite_type, BigInteger), (table.name, column.name)
