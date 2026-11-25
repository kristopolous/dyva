"""Private vs public chat reads — pytest (`uv run pytest tests/test_private_chats.py -q`).

Once a chat is stamped with a userhash (claim-once provenance, see test_chat_userhash_provenance),
reads are scoped so the sidebar can show a Private / Public split:

  * scope="public"  -> only global (userhash IS NULL) chats — the shared feed everyone sees,
    and all a signed-out caller ever sees;
  * scope="private" -> only the signed-in caller's OWN chats (userhash = theirs); with no
    identity it's empty.

Opening or deleting by id is gated the same way: a public chat is reachable by anyone, an owned
chat only by its owner, and a miss and a hidden chat look identical (both None/404) so a guessed
id can't confirm a private chat exists. Explicit share links (/docs/{cid}/{doc}) are a separate,
deliberately-public surface and are NOT gated here.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402

import pytest  # noqa: E402


@pytest.fixture
def cdb(tmp_path, monkeypatch):
    monkeypatch.setattr(dyva, "CHATS_DB", str(tmp_path / "chats.db"))
    monkeypatch.setattr(dyva, "_chats_db", None)
    yield


def _seed():
    dyva._chat_upsert("pub1", {"title": "public one", "messages": []})        # NULL == global
    dyva._chat_upsert("a1", {"title": "alice one", "messages": []}, "alice")
    dyva._chat_upsert("a2", {"title": "alice two", "messages": []}, "alice")
    dyva._chat_upsert("b1", {"title": "bob one", "messages": []}, "bob")


# ---- list scoping ----------------------------------------------------------

def test_public_list_shows_only_global(cdb):
    _seed()
    assert {c["id"] for c in dyva._chats_list(scope="public")} == {"pub1"}


def test_private_list_shows_only_my_own(cdb):
    _seed()
    assert {c["id"] for c in dyva._chats_list("alice", scope="private")} == {"a1", "a2"}


def test_private_list_without_identity_is_empty(cdb):
    _seed()
    assert dyva._chats_list(None, scope="private") == []


# ---- get-by-id ownership ---------------------------------------------------

def test_public_chat_opens_for_anyone(cdb):
    _seed()
    assert dyva._chat_get_scoped("pub1", None)["title"] == "public one"
    assert dyva._chat_get_scoped("pub1", "bob")["title"] == "public one"


def test_owned_chat_opens_only_for_its_owner(cdb):
    _seed()
    assert dyva._chat_get_scoped("a1", "alice")["title"] == "alice one"
    assert dyva._chat_get_scoped("a1", "bob") is None       # someone else's private chat
    assert dyva._chat_get_scoped("a1", None) is None        # signed-out can't open it
    assert dyva._chat_get_scoped("nope", "alice") is None   # missing looks the same


# ---- delete ownership ------------------------------------------------------

def test_owned_chat_deletes_only_for_its_owner(cdb):
    _seed()
    assert dyva._chat_delete("a1", "bob") is False          # refused — not bob's
    assert dyva._chat_get_scoped("a1", "alice") is not None # still there
    assert dyva._chat_delete("a1", "alice") is True         # owner may
    assert dyva._chat_get_scoped("a1", "alice") is None


def test_public_chat_deletes_for_anyone(cdb):
    _seed()
    assert dyva._chat_delete("pub1", None) is True          # unchanged: public is open


# ---- search scoping --------------------------------------------------------

def test_search_is_scoped(cdb):
    dyva._chat_upsert("pub1", {"title": "tomato public", "messages": []})
    dyva._chat_upsert("a1", {"title": "tomato alice", "messages": []}, "alice")
    assert {h["id"] for h in dyva._chats_search("tomato", scope="public")} == {"pub1"}
    assert {h["id"] for h in dyva._chats_search("tomato", userhash="alice", scope="private")} == {"a1"}


# ---- the visibility helper the semantic-search post-filter uses ------------

def test_chat_visible_helper(cdb):
    _seed()
    assert dyva._chat_visible("pub1", None, "public") is True
    assert dyva._chat_visible("a1", "alice", "private") is True
    assert dyva._chat_visible("a1", "bob", "private") is False
    assert dyva._chat_visible("a1", None, "public") is False   # a private chat isn't in the feed


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
