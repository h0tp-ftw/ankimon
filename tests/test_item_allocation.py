"""The real SQLite item allocator must serialize competing writers."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch

import pytest

from Ankimon.pyobj.database_manager import AnkimonDB


def test_concurrent_uncatalogued_items_keep_both_stacks(tmp_path):
    path = tmp_path / "items.db"
    first, second = AnkimonDB(db_path=path), AnkimonDB(db_path=path)
    allocated, competing, finished = Event(), Event(), Event()

    def write_first():
        conn = first._get_connection()
        cursor = conn.cursor()

        class PausedAllocation:
            def execute(self, sql, parameters=()):
                result = cursor.execute(sql, parameters)
                if "SELECT MIN(id)" in sql:
                    allocated.set()
                    assert competing.wait(5)
                    # Without a writer lock the competitor finishes and claims
                    # the very same ID. With a lock it waits until we commit.
                    finished.wait(0.3)
                return result

            def __getattr__(self, name):
                return getattr(cursor, name)

        with patch.object(conn, "cursor", return_value=PausedAllocation()):
            return first.save_item(None, "custom-a", 3)

    def write_second():
        assert allocated.wait(5)
        competing.set()
        try:
            return second.save_item(None, "custom-b", 7)
        finally:
            finished.set()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            a, b = pool.submit(write_first), pool.submit(write_second)
            assert a.result(timeout=10)
            assert b.result(timeout=10)
        assert first.get_item("custom-a")["quantity"] == 3
        assert first.get_item("custom-b")["quantity"] == 7
        assert first.get_item("custom-a")["id"] != first.get_item("custom-b")["id"]
    finally:
        first.close()
        second.close()


@pytest.mark.parametrize("outer", [False, True])
def test_item_allocation_commit_false_is_rollbackable(tmp_path, outer):
    db = AnkimonDB(db_path=tmp_path / "items.db")
    conn = db._get_connection()
    try:
        if outer:
            conn.execute("BEGIN")
            conn.execute("INSERT INTO metadata VALUES ('caller', 'pending')")
        assert db.save_item(None, "custom-a", 3, commit=False)
        assert db.save_item(None, "custom-b", 7, commit=False)
        assert conn.in_transaction
        conn.rollback()
        assert db.get_all_items() == []
        assert not conn.execute(
            "SELECT 1 FROM metadata WHERE key = 'caller'"
        ).fetchone()
    finally:
        db.close()


def test_failed_item_write_preserves_caller_transaction(tmp_path):
    db = AnkimonDB(db_path=tmp_path / "items.db")
    conn = db._get_connection()
    try:
        db.save_item(1, "old-custom", 3)
        conn.execute("BEGIN")
        conn.execute("INSERT INTO metadata VALUES ('caller', 'pending')")
        with pytest.raises(TypeError):
            db.save_item(1, "catalogue", 7, extra_data={"bad": object()}, commit=False)
        assert conn.in_transaction
        assert conn.execute("SELECT 1 FROM metadata WHERE key = 'caller'").fetchone()
        assert db.get_item("old-custom")["id"] == 1
        conn.rollback()
    finally:
        db.close()
