"""userhash provenance on the global user-chats store (`uv run pytest
tests/test_chat_userhash_provenance.py -q`).

Groundwork for attributing demo chats to a user. Each chat row gains a nullable `userhash`
column: NULL means GLOBAL (the current, unchanged behavior). Provenance is stamped at save
time from the request's env-bag and is claim-once / never-steal — a global (NULL) chat can be
claimed by the first identified saver, but an owned chat is never reassigned or un-claimed by a
later save. Reads are UNCHANGED (everything is still global/visible for now); this only lands
the column the plans depend on. See brainstorm/user-account.md.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402

import pytest   # noqa: E402


@pytest.fixture
def chats_db(tmp_path, monkeypatch):
    monkeypatch.setattr(dyva, "CHATS_DB", str(tmp_path / "chats.db"))
    monkeypatch.setattr(dyva, "_chats_db", None)
    yield


def _uh(cid):
    return dyva._get_chats_db().execute(
        "SELECT userhash FROM chats WHERE id=?", (str(cid),)).fetchone()[0]


def test_schema_has_a_userhash_column(chats_db):
    cols = [r[1] for r in dyva._get_chats_db().execute("PRAGMA table_info(chats)").fetchall()]
    assert "userhash" in cols


def test_anonymous_save_is_global_null(chats_db):
    dyva._chat_upsert("c1", {"title": "hi", "messages": []})
    assert _uh("c1") is None                      # NULL == global


def test_save_stamps_the_userhash(chats_db):
    dyva._chat_upsert("c1", {"title": "hi", "messages": []}, "uh1")
    assert _uh("c1") == "uh1"


def test_global_chat_is_claimed_once_by_first_identified_saver(chats_db):
    dyva._chat_upsert("c1", {"title": "hi"})            # starts global
    assert _uh("c1") is None
    dyva._chat_upsert("c1", {"title": "hi2"}, "uh1")    # first identified save claims it
    assert _uh("c1") == "uh1"


def test_owned_chat_is_never_stolen_or_unclaimed(chats_db):
    dyva._chat_upsert("c1", {"title": "hi"}, "uh1")
    dyva._chat_upsert("c1", {"title": "edit"}, "uh2")   # another user saves same id
    assert _uh("c1") == "uh1"                            # not reassigned
    dyva._chat_upsert("c1", {"title": "edit2"})         # anonymous save
    assert _uh("c1") == "uh1"                            # not un-claimed


def test_reads_stay_global_regardless_of_provenance(chats_db):
    dyva._chat_upsert("c1", {"title": "mine"}, "uh1")
    dyva._chat_upsert("c2", {"title": "global"})
    ids = {c["id"] for c in dyva._chats_list()}
    assert ids == {"c1", "c2"}                           # both listed — no filtering yet


def test_existing_db_migrates_old_rows_to_null(tmp_path, monkeypatch):
    # a pre-provenance chats.db (no userhash column) must gain the column, old rows -> NULL
    path = str(tmp_path / "chats.db")
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE chats(id TEXT PRIMARY KEY, title TEXT, created REAL, data TEXT NOT NULL)")
    old.execute("INSERT INTO chats VALUES('old','t',1.0,'{}')")
    old.commit()
    old.close()
    monkeypatch.setattr(dyva, "CHATS_DB", path)
    monkeypatch.setattr(dyva, "_chats_db", None)
    cols = [r[1] for r in dyva._get_chats_db().execute("PRAGMA table_info(chats)").fetchall()]
    assert "userhash" in cols
    assert _uh("old") is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
