"""A starter pack's install marker is not a corrupt repo index.

#417 (@MotoMato85), finding 2. `IndexStore.list_repos` globbed `*.json` in the
storage root, and `pathlib.glob` matches DOTFILES — unlike the shell glob most
people picture when they read that line. So `.pack-<id>.json`, the marker
`install-pack` writes, was read as a repo index, split into owner `.pack`, and
rejected by the migration schema guard.

⚠ The output was not merely noisy, it was ACTIVELY HARMFUL, which is what makes
this more than cosmetic:

    Migration schema validation failed for .pack/nodejs — missing required
    fields (stale or corrupt JSON index; remove it with
    `jcodemunch-mcp delete-index .pack/nodejs`)

`_repo_slug(".pack", "nodejs")` round-trips to `.pack-nodejs.json` — the marker
itself — so a user following that advice deletes their pack's install record
while its indexes stay on disk. The "already installed" check then goes blind.

Reproduced on the maintainer's own box before the fix: three markers, three
lines, on every CLI invocation.
"""
from __future__ import annotations

import json

from jcodemunch_mcp.storage.index_store import IndexStore


def _marker(base, pack_id):
    """What `install-pack` actually writes (install_pack.py `_install_pack`)."""
    p = base / f".pack-{pack_id}.json"
    p.write_text(
        json.dumps({"pack": pack_id, "version": "2026.08.03"}), encoding="utf-8"
    )
    return p


def test_pathlib_glob_matches_dotfiles(tmp_path):
    """The premise, pinned. If this ever stops being true the fix is arguably
    unnecessary — but nobody should have to rediscover it to find that out."""
    (tmp_path / ".pack-nodejs.json").write_text("{}", encoding="utf-8")
    assert [p.name for p in tmp_path.glob("*.json")] == [".pack-nodejs.json"]


def test_a_pack_marker_is_not_listed_as_a_repo(tmp_path):
    _marker(tmp_path, "nodejs")
    _marker(tmp_path, "fastapi")

    repos = IndexStore(base_path=str(tmp_path)).list_repos()

    assert [r for r in repos if str(r.get("owner", "")).startswith(".")] == [], (
        f"a dot-prefixed marker was returned as a repo: {repos}"
    )


def test_no_corrupt_index_warning_for_a_pack_marker(tmp_path, caplog):
    """The user-visible half, and the only assertion here that ever FAILED
    against pre-fix code. The warning is what sends someone to the destructive
    command, so its absence is the actual guarantee.

    ⚠ Two things make this easy to write vacuously, and the first draft of this
    file did both. The message goes through `logging`, so `capsys` never sees it
    and any capsys assertion passes for the wrong reason. And the emitter
    dedupes on `(owner, name)` in a PROCESS-global set, so a second test using
    the same pack id is silently suppressed — hence the unique id and the
    explicit clear below.
    """
    from jcodemunch_mcp.storage import sqlite_store

    sqlite_store._MIGRATION_SCHEMA_WARNED.discard((".pack", "markertest"))
    _marker(tmp_path, "markertest")

    with caplog.at_level("WARNING"):
        IndexStore(base_path=str(tmp_path)).list_repos()

    text = caplog.text.lower()
    assert "migration schema validation failed" not in text, (
        f"the bogus corrupt-index warning still fires: {caplog.text!r}"
    )
    assert "delete-index" not in text, (
        "the output still points at the command that deletes the marker"
    )


def test_the_advice_would_have_deleted_the_marker(tmp_path):
    """Why this was worth a fix rather than a filter on the message.

    Pins the round-trip that makes the printed advice destructive. If a future
    change to `_repo_slug` breaks this identity the hazard is gone, and whoever
    sees this test go red should read this docstring before 'fixing' it.
    """
    store = IndexStore(base_path=str(tmp_path))
    marker = _marker(tmp_path, "nodejs")

    assert store._index_path(".pack", "nodejs") == marker, (
        "delete-index .pack/nodejs no longer resolves to the marker file"
    )


def test_a_real_json_index_is_still_listed(tmp_path):
    """Non-vacuity, and the guarantee itself: the legacy-JSON migration path
    must keep working. A fix that skipped every *.json would pass all four
    tests above and silently strand every pre-SQLite index."""
    (tmp_path / "someowner-somerepo.json").write_text(
        json.dumps({
            # `repo` is what `_repo_entry_from_data` keys on — without it the
            # entry is dropped as unreadable regardless of this fix, which is
            # how the first version of this test failed against BOTH trees.
            "repo": "someowner/somerepo",
            "indexed_at": "2026-08-06T00:00:00",
            "symbols": [],
            "source_files": [],
        }),
        encoding="utf-8",
    )
    _marker(tmp_path, "nodejs")

    repos = IndexStore(base_path=str(tmp_path)).list_repos()

    assert "someowner/somerepo" in {r.get("repo") for r in repos}, (
        f"the ordinary JSON index stopped being listed too: {repos}"
    )
