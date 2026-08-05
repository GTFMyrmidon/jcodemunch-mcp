"""A rebuild the caller did not ask for must be reportable as data (#413).

Reported by @LuigiNicaPRO against v1.108.231. ``load_index`` returns ``None``
on SEVEN distinct paths; ``index_folder`` collapsed all of them into one
branch that (a) named a downgrade as the cause and told the user to delete
their whole index directory, (b) silently substituted a full rebuild for the
requested incremental, and (c) re-stamped ``meta.index_version`` on the way
out -- so after the call the database was indistinguishable from one that had
always been healthy. The substitution erased its own evidence.

``inspect_index`` (PR #291) already discriminates the causes for the READ
side. These tests pin it on the WRITE side, and pin the substitution as fields
a caller can branch on rather than a sentence in ``warnings[]``.

The empty-meta case is the one that had NO message at all, so it is the case
that proves this is not merely a wording change.
"""
from __future__ import annotations

import sqlite3

import pytest

from jcodemunch_mcp.tools.index_folder import index_folder

MARKER = "unloadable_marker_fn"


def _index(src, store, **kw):
    return index_folder(
        path=str(src), storage_path=str(store), use_ai_summaries=False, **kw
    )


@pytest.fixture()
def healthy(tmp_path):
    """A healthy index plus the path to its database."""
    src = tmp_path / "proj"
    src.mkdir()
    (src / "a.py").write_text(f"def {MARKER}():\n    return 1\n", encoding="utf-8")
    store = tmp_path / "store"

    first = _index(src, store)
    assert first.get("success") is True
    db = next(store.glob("*.db"))
    return src, store, db


def _damage(db, sql):
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(sql)
        conn.commit()
    finally:
        conn.close()


DAMAGE = [
    (
        "future_version",
        "UPDATE meta SET value='999999' WHERE key='index_version'",
        "sqlite_future_version",
    ),
    (
        "corrupt_version",
        "UPDATE meta SET value='x' WHERE key='index_version'",
        "sqlite_corrupt",
    ),
    ("empty_meta", "DELETE FROM meta", "sqlite_missing_meta"),
]


@pytest.mark.parametrize(
    "label,sql,expected", DAMAGE, ids=[d[0] for d in DAMAGE]
)
def test_unloadable_index_names_its_own_cause(healthy, label, sql, expected):
    src, store, db = healthy
    _damage(db, sql)

    out = _index(src, store, incremental=True)

    assert out.get("success") is True, out
    assert out.get("rebuild_reason") == expected, (
        f"{label}: rebuild_reason should name the measured cause, got "
        f"{out.get('rebuild_reason')!r}"
    )
    assert out.get("requested_incremental") is True
    assert out.get("performed_incremental") is False, (
        f"{label}: a full rebuild must not report itself as the incremental "
        "the caller requested"
    )

    warnings = out.get("warnings") or []
    assert any(expected in w for w in warnings), (
        f"{label}: no warning names the cause: {warnings!r}"
    )
    if expected != "sqlite_future_version":
        # The whole defect: a corrupt or empty-meta index was reported as a
        # downgrade, with "delete your index directory" as the remedy.
        assert not any("newer version" in w for w in warnings), (
            f"{label}: still blames a downgrade: {warnings!r}"
        )


def test_downgrade_keeps_its_downgrade_remedy(healthy):
    """The one cause that IS a downgrade must keep saying so."""
    src, store, db = healthy
    _damage(db, "UPDATE meta SET value='999999' WHERE key='index_version'")

    out = _index(src, store, incremental=True)

    warnings = " ".join(out.get("warnings") or [])
    assert "downgraded" in warnings, warnings
    assert "newer version" in warnings, warnings
    assert "CODE_INDEX_PATH" in warnings, warnings


def test_healthy_incremental_is_not_reported_as_a_rebuild(healthy):
    """Non-vacuity: the fields must be able to say 'nothing was substituted'."""
    src, store, _db = healthy
    (src / "b.py").write_text("def other():\n    return 2\n", encoding="utf-8")

    out = _index(src, store, incremental=True)

    assert out.get("success") is True, out
    assert out.get("performed_incremental") is True, out
    assert out.get("requested_incremental") is True
    assert "rebuild_reason" not in out, out.get("rebuild_reason")


def test_first_index_reports_a_full_run_without_inventing_a_cause(tmp_path):
    """No index to diff against is not an unreadable index."""
    src = tmp_path / "fresh"
    src.mkdir()
    (src / "a.py").write_text("def only():\n    return 1\n", encoding="utf-8")

    out = _index(src, tmp_path / "store", incremental=True)

    assert out.get("success") is True, out
    assert out.get("requested_incremental") is True
    assert out.get("performed_incremental") is False
    assert "rebuild_reason" not in out, (
        "a first-time index has no unreadable index to blame"
    )


def test_full_run_the_caller_asked_for_is_not_a_substitution(healthy):
    src, store, _db = healthy

    out = _index(src, store, incremental=False)

    assert out.get("requested_incremental") is False
    assert out.get("performed_incremental") is False
    assert "rebuild_reason" not in out
