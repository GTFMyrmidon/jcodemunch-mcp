"""`config --check` must report the value the runtime actually resolves (#416 follow-up).

@domis86 reported that with a `.jcodemunch.jsonc` present but NOT declaring
`max_folder_files`, `config --check` printed the hardcoded default `2000` and
tagged the row `[config]` while global config said something else. Indexing was
correct throughout: the walk honoured the global value. Only the diagnostic lied,
which is the exact inverse of #416 and strictly harder to trust, because
`config --check` is the tool users are pointed at to debug this class of problem.

Root cause was a load-order dependency. `_PROJECT_CONFIGS` held
`deepcopy(_GLOBAL_CONFIG)` overlaid with the project's keys, so the value of every
UNDECLARED key was frozen at whatever global happened to contain when
`load_project_config()` ran. `config --check` loads project config before
`load_config()`; indexing loads it after. Same code, two answers.

⚠ THE DEFECT CLASS, not the key. This is the third time a `config --check` row has
disagreed with the runtime it describes: #300/#304 (`summarizer_model`), #393
(`use_ai_summaries`, `summarizer_provider`), now the three indexing limits. Each
was fixed one row at a time. The parametrized guards below are written against the
SET of limits and against BOTH load orders, so the next row to drift fails here
instead of arriving as issue four.

⚠ NON-VACUITY. Against pre-fix code `test_check_row_matches_global_when_project_omits_key`
and `test_resolver_is_load_order_independent` fail for all three limits, and
`test_global_edit_reaches_file_backed_project` fails. The two control tests
(`test_project_declaration_still_wins`, `test_no_project_file_reads_global`) pass on
BOTH sides — they are what stops this file going green because project config, or
capping, quietly stopped working altogether.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

SRC = str(Path(__file__).resolve().parents[1] / "src")

# Every limit resolved through the project-aware config path. A limit that gains
# a `repo=`-aware resolver without being added here is the gap this file exists
# to close.
INDEXING_LIMITS = ("max_file_size", "max_folder_files", "max_index_files")

# Values chosen so nothing collides with a hardcoded default (512000 / 2000 / 10000).
GLOBAL_VALUES = {"max_file_size": 99_999, "max_folder_files": 3, "max_index_files": 4_321}

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


@pytest.fixture
def env(tmp_path):
    """An isolated store + a project dir, with global config set and no project file.

    CODE_INDEX_PATH isolation is load-bearing: without it the subprocess reads the
    developer's real ~/.code-index/config.jsonc and these assertions turn into a
    property of whoever is running them (#411, @rknighton).
    """
    store = tmp_path / "store"
    proj = tmp_path / "proj"
    (proj / "src").mkdir(parents=True)
    store.mkdir()
    for i in range(5):
        (proj / "src" / f"m{i}.py").write_text(f"def f{i}():\n    return {i}\n")
    (store / "config.jsonc").write_text(json.dumps(GLOBAL_VALUES, indent=2))

    e = {**os.environ, "PYTHONPATH": SRC, "CODE_INDEX_PATH": str(store)}
    # The env routes are separate channels with their own precedence; leaving a
    # developer's export in place would mask the config route under test.
    for var in ("JCODEMUNCH_MAX_FILE_SIZE", "JCODEMUNCH_MAX_FOLDER_FILES",
                "JCODEMUNCH_MAX_INDEX_FILES"):
        e.pop(var, None)
    return {"env": e, "store": store, "proj": proj}


def _write_project_config(proj: Path, body: dict) -> None:
    (proj / ".jcodemunch.jsonc").write_text(json.dumps(body, indent=2))


def _run(env, args, cwd):
    proc = subprocess.run(
        [sys.executable, "-m", "jcodemunch_mcp", *args],
        capture_output=True, text=True, env=env, cwd=str(cwd), timeout=180,
    )
    return _ANSI.sub("", proc.stdout + proc.stderr)


def _row(out: str, key: str):
    """Return (value, source_tag) for a `config --check` row, or None."""
    m = re.search(rf"^\s*{re.escape(key)}\s+(\S+)(?:\s+\[(\w+)\]|\s+\((\w+)\))?\s*$", out, re.M)
    if not m:
        return None
    return m.group(1), (m.group(2) or m.group(3))


def _resolve_in_subprocess(env, proj: Path, key: str, order: str) -> int:
    """Call the runtime resolver directly, choosing the config load order.

    `order` is the whole point: 'global_first' is what indexing does,
    'project_first' is what `config --check` does. A correct resolver cannot
    tell the difference.
    """
    prog = f"""
import json
from pathlib import Path
from jcodemunch_mcp import config as c
from jcodemunch_mcp.security import (
    get_max_file_size, get_max_folder_files, get_max_index_files,
)
root = str(Path({str(proj)!r}).resolve())
if {order!r} == "global_first":
    c.load_config(); c.load_project_config(root)
else:
    c.load_project_config(root); c.load_config()
fn = {{"max_file_size": get_max_file_size,
      "max_folder_files": get_max_folder_files,
      "max_index_files": get_max_index_files}}[{key!r}]
print(json.dumps({{"v": fn(repo=root)}}))
"""
    proc = subprocess.run([sys.executable, "-c", prog], capture_output=True,
                          text=True, env=env, cwd=str(proj), timeout=120)
    assert proc.returncode == 0, f"resolver probe failed:\n{proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])["v"]


# ── The reported row must equal the effective value ───────────────────────────

@pytest.mark.parametrize("key", INDEXING_LIMITS)
def test_check_row_matches_global_when_project_omits_key(env, key):
    """A project file that declares OTHER keys must not blank out this one.

    Pre-fix this printed the hardcoded default with a `[config]` tag: a wrong
    value wearing an authoritative source tag, which is worse than no row at all.
    """
    _write_project_config(env["proj"], {"extra_ignore_patterns": ["docs/legacy/"]})
    out = _run(env["env"], ["config", "--check"], env["proj"])
    row = _row(out, key)
    assert row, f"{key} row missing from:\n{out}"
    value, source = row
    assert value == str(GLOBAL_VALUES[key]), (
        f"`config --check` reports {key}={value} but global config says "
        f"{GLOBAL_VALUES[key]}. The project file does not declare {key}, so the "
        "global value is the effective one. Full output:\n" + out
    )
    assert source == "config", (
        f"{key} came from the global config file but is tagged [{source}]."
    )


@pytest.mark.parametrize("key", INDEXING_LIMITS)
def test_resolver_is_load_order_independent(env, key):
    """The resolver must not depend on when project config was loaded.

    This is the root cause stated directly. `config --check` and indexing load in
    opposite orders, so any snapshot taken at load time makes them disagree.
    """
    _write_project_config(env["proj"], {"extra_ignore_patterns": ["docs/legacy/"]})
    global_first = _resolve_in_subprocess(env["env"], env["proj"], key, "global_first")
    project_first = _resolve_in_subprocess(env["env"], env["proj"], key, "project_first")
    assert global_first == project_first == GLOBAL_VALUES[key], (
        f"{key} resolves to {global_first} when global is loaded first and "
        f"{project_first} when project is loaded first; both should be "
        f"{GLOBAL_VALUES[key]}."
    )


def test_check_row_matches_what_indexing_actually_did(env):
    """Tie the diagnostic to observed behaviour, not to another read of config.

    Comparing two config reads can agree while both are wrong. The index run
    reports the cap it enforced, so this asserts the row against a number
    produced by the walk itself.
    """
    _write_project_config(env["proj"], {"extra_ignore_patterns": ["docs/legacy/"]})
    reported = _row(_run(env["env"], ["config", "--check"], env["proj"]), "max_folder_files")
    assert reported, "max_folder_files row missing"

    out = _run(env["env"], ["index", "."], env["proj"])
    m = re.search(r"max_folder_files=(\d+)", out)
    assert m, f"index did not report the cap it enforced:\n{out[-3000:]}"
    enforced = m.group(1)

    assert reported[0] == enforced, (
        f"`config --check` says max_folder_files={reported[0]} but the walk "
        f"enforced {enforced}. The diagnostic must describe the runtime."
    )


def test_global_edit_reaches_file_backed_project(env):
    """A later global-config edit must be visible to a repo that HAS a project file.

    v1.108.197 fixed this for repos with no project file. The file-backed branch
    kept a frozen copy of global and was explicitly excluded from that refresh, so
    the same defect survived one branch over.
    """
    _write_project_config(env["proj"], {"extra_ignore_patterns": ["docs/legacy/"]})
    prog = f"""
import json
from pathlib import Path
from jcodemunch_mcp import config as c
from jcodemunch_mcp.security import get_max_folder_files
root = str(Path({str(env["proj"])!r}).resolve())
c.load_config(); c.load_project_config(root)
before = get_max_folder_files(repo=root)
Path({str(env["store"] / "config.jsonc")!r}).write_text('{{"max_folder_files": 7777}}')
c.load_config(); c.load_project_config(root)
print(json.dumps({{"before": before, "after": get_max_folder_files(repo=root)}}))
"""
    proc = subprocess.run([sys.executable, "-c", prog], capture_output=True,
                          text=True, env=env["env"], cwd=str(env["proj"]), timeout=120)
    assert proc.returncode == 0, proc.stderr
    got = json.loads(proc.stdout.strip().splitlines()[-1])
    assert got["before"] == GLOBAL_VALUES["max_folder_files"]
    assert got["after"] == 7777, (
        f"global config changed to 7777 but the repo still resolves "
        f"{got['after']}; the project entry froze a copy of global."
    )


# ── Controls: these pass on BOTH sides of the fix ─────────────────────────────

def test_project_declaration_still_wins(env):
    """A key the project file DOES declare must override global.

    Without this, the file above could go green by project config ceasing to be
    consulted at all.
    """
    _write_project_config(env["proj"], {
        "extra_ignore_patterns": ["docs/legacy/"], "max_folder_files": 9_000,
    })
    out = _run(env["env"], ["config", "--check"], env["proj"])
    assert _row(out, "max_folder_files") == ("9000", "project"), (
        f"project-declared max_folder_files not honoured:\n{out}"
    )


def test_no_project_file_reads_global(env):
    """With no project file at all, global is the effective value (#390 / .197)."""
    out = _run(env["env"], ["config", "--check"], env["proj"])
    assert _row(out, "max_folder_files") == ("3", "config"), (
        f"global max_folder_files not reported with no project file:\n{out}"
    )


def test_cap_is_actually_enforced(env):
    """The walk still truncates at the resolved cap.

    The control for the whole file: every assertion above compares a REPORTED
    number against another number, and all of them would agree if capping stopped
    working and every value read 3 for the wrong reason.
    """
    _write_project_config(env["proj"], {"extra_ignore_patterns": ["docs/legacy/"]})
    out = _run(env["env"], ["index", "."], env["proj"])
    payload = json.loads(out[out.index("{"):out.rindex("}") + 1])
    assert payload["files_discovered"] == 5, payload
    assert payload["files_indexed"] == 3, payload
    assert payload["files_skipped_cap"] == 2, payload
    assert payload["truncated"] is True, payload
