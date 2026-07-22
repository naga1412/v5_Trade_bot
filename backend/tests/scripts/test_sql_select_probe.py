"""Unit tests for the ops-debug sql-select probe's shape validator.

The DB-level layers (SET SESSION CHARACTERISTICS ... READ ONLY,
statement_timeout, cursor row cap) require a live Postgres — SQLite
does not honor these primitives. Those layers are covered by the
integration soak on prod. What we CAN pin in unit tests is that the
client-side gate rejects every DDL / DML / multi-statement / non-token
input before the DB is ever contacted.
"""
from __future__ import annotations

import pytest

from scripts.sql_select_probe import strip_sql_line_comments, validate_shape


class TestStripSqlLineComments:
    def test_leaves_single_line_untouched(self) -> None:
        assert strip_sql_line_comments("SELECT 1") == "SELECT 1"

    def test_strips_trailing_line_comment(self) -> None:
        assert strip_sql_line_comments("SELECT 1 -- pick a number") == "SELECT 1 "

    def test_strips_leading_line_comment(self) -> None:
        assert strip_sql_line_comments("-- header\nSELECT 1") == "\nSELECT 1"

    def test_strips_per_line(self) -> None:
        got = strip_sql_line_comments("SELECT a, -- col a\n       b\nFROM t")
        assert got == "SELECT a, \n       b\nFROM t"


class TestValidateShape:
    def test_accepts_bare_select(self) -> None:
        ok, reason = validate_shape("SELECT 1")
        assert ok, reason

    def test_accepts_lowercase_select(self) -> None:
        ok, reason = validate_shape("select 1")
        assert ok, reason

    def test_accepts_with_cte(self) -> None:
        sql = "WITH x AS (SELECT 1) SELECT * FROM x"
        ok, reason = validate_shape(sql)
        assert ok, reason

    def test_accepts_leading_whitespace_and_comments(self) -> None:
        sql = "-- header\n   SELECT 1"
        ok, reason = validate_shape(sql)
        assert ok, reason

    def test_accepts_trailing_semicolon(self) -> None:
        ok, reason = validate_shape("SELECT 1;")
        assert ok, reason

    def test_rejects_empty(self) -> None:
        ok, reason = validate_shape("")
        assert not ok
        assert "empty" in reason

    def test_rejects_whitespace_only(self) -> None:
        ok, reason = validate_shape("   \n\t  ")
        assert not ok
        assert "empty" in reason

    def test_rejects_comment_only(self) -> None:
        ok, reason = validate_shape("-- just a comment")
        assert not ok
        assert "empty" in reason

    @pytest.mark.parametrize("stmt", [
        "INSERT INTO foo VALUES (1)",
        "UPDATE foo SET x=1",
        "DELETE FROM foo",
        "DROP TABLE foo",
        "TRUNCATE foo",
        "CREATE TABLE foo (x int)",
        "ALTER TABLE foo ADD COLUMN y int",
        "GRANT ALL ON foo TO trading",
        "COPY foo FROM stdin",
        "VACUUM foo",
        "SET default_transaction_read_only = off",
    ])
    def test_rejects_dml_ddl_utility(self, stmt: str) -> None:
        ok, reason = validate_shape(stmt)
        assert not ok
        assert "SELECT/WITH permitted" in reason

    def test_rejects_multiple_statements(self) -> None:
        ok, reason = validate_shape("SELECT 1; SELECT 2")
        assert not ok
        assert "multiple statements" in reason

    def test_rejects_select_with_hidden_dml(self) -> None:
        """A SELECT prefix cannot smuggle DML in as a second statement."""
        ok, reason = validate_shape("SELECT 1; DELETE FROM live_trades")
        assert not ok
        assert "multiple statements" in reason
