"""The sdist exclude list is proven by BUILDING, not by grepping pyproject.toml.

`tests/test_build.py` asserts the exclude strings are present in the config and
scans `dist/` if something happens to be sitting there. Both checks pass in a
state where the exclusion does not work:

- a config assertion tests the text we just wrote, not the artifact hatchling
  produces from it;
- the `dist/` scan `return`s when the directory is empty, which is exactly the
  state of CI's fresh clone. That guard has never once failed, and it never can.

So it went unnoticed that the sdist carried `vscode-extension/node_modules/`
(128 entries) and the extension's compiled `out/` through every release up to
1.108.245. Nothing on the tree side could have shown it: those paths are covered
by `vscode-extension/.gitignore`, git therefore never tracked them, and
**hatchling reads the filesystem while honouring only the ROOT `.gitignore`.**
`.claude/` reached the sdist the same way once, via the global gitignore.

This module PLANTS a canary under each excluded path, builds a real sdist into a
temp directory, and requires the canary to be absent. A broken exclude rule fails
here. The reverse assertion matters just as much and is tested alongside: a build
that excluded the extension SOURCE would also produce a canary-free tarball, and
would be a different defect -- an sdist has to be able to rebuild the project.

Failure here means: fix `[tool.hatch.build.targets.sdist] exclude` in
pyproject.toml. Do not fix it by deleting the canary.
"""
from __future__ import annotations

import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

CANARY_NAME = "__sdist_exclusion_canary__.txt"

# Each entry is a directory that must be excluded from the sdist. The canary is
# planted inside it, so a rule that stops matching leaks a file with an obvious
# name rather than silently re-admitting megabytes of real content.
EXCLUDED_DIRS = [
    ".claude",
    "vscode-extension/node_modules",
    "vscode-extension/out",
]

# Paths that must SURVIVE the build. Without these the test would pass on a
# pyproject that excluded far too much.
REQUIRED_PATHS = [
    "pyproject.toml",
    "README.md",
    "src/jcodemunch_mcp/server.py",
    "vscode-extension/package.json",
    "vscode-extension/src/extension.ts",
]


def _build_sdist(outdir: Path) -> subprocess.CompletedProcess:
    """Build an sdist with whatever builder this environment actually has.

    ⚠ `python -m build` is NOT present in CI: the job installs dependencies
    with `uv sync --locked --group dev`, and `build` is not in that group.
    Adding it would mean re-locking, and `uv lock` is off-limits here (it
    rewrites unrelated marker lines and has twice restripped `revision = 3`).
    CI does have `uv`, which the workflow already uses to build an sdist.

    ⚠⚠ **This deliberately does not skip when no builder is found.** A skip is
    how the guard this module replaces became decorative -- it returned early
    in exactly the environment that matters. If neither builder exists the
    tests error, which is the honest outcome: the exclusion went unverified.
    """
    attempts: list[str] = []
    for argv in (
        [sys.executable, "-m", "build", "--sdist", "--outdir", str(outdir)],
        ["uv", "build", "--sdist", "--out-dir", str(outdir)],
    ):
        try:
            result = subprocess.run(
                argv, cwd=REPO_ROOT, capture_output=True, text=True
            )
        except FileNotFoundError:
            attempts.append(f"{argv[0]}: not on PATH")
            continue
        # "No module named build" is an absent builder, not a build failure.
        if result.returncode != 0 and "No module named build" in result.stderr:
            attempts.append(f"{argv[0]} -m build: module not installed")
            continue
        return result

    raise AssertionError(
        "no sdist builder available, so the exclude rules went unverified: "
        + "; ".join(attempts)
    )


@pytest.fixture(scope="module")
def built_sdist(tmp_path_factory) -> list[str]:
    """Plant the canaries, build one sdist, return its member names.

    Module-scoped: `python -m build` sets up an isolated env and takes tens of
    seconds, and every assertion below reads the same tarball.
    """
    planted: list[Path] = []
    created_dirs: list[Path] = []
    try:
        for rel in EXCLUDED_DIRS:
            directory = REPO_ROOT / rel
            if not directory.exists():
                # CI clones without node_modules/out; create them so the rule is
                # exercised there too. Tracked back for removal in the `finally`.
                directory.mkdir(parents=True)
                created_dirs.append(directory)
            canary = directory / CANARY_NAME
            canary.write_text("planted by test_sdist_exclusions\n", encoding="utf-8")
            planted.append(canary)

        outdir = tmp_path_factory.mktemp("sdist")
        result = _build_sdist(outdir)
        assert result.returncode == 0, (
            "sdist build failed:\n" + result.stdout[-4000:] + "\n" + result.stderr[-4000:]
        )

        tarballs = list(outdir.glob("*.tar.gz"))
        assert len(tarballs) == 1, f"expected one sdist, got {[t.name for t in tarballs]}"
        with tarfile.open(tarballs[0], "r:gz") as tf:
            members = tf.getnames()
    finally:
        for canary in planted:
            canary.unlink(missing_ok=True)
        # Deepest first, and only ones this test created. rmdir refuses a
        # non-empty directory, so a real node_modules is never removed.
        for directory in sorted(created_dirs, reverse=True):
            try:
                directory.rmdir()
            except OSError:
                pass

    assert members, "sdist contained no members"
    return members


def _strip_root(members: list[str]) -> list[str]:
    """Drop the `jcodemunch_mcp-<version>/` prefix every member carries."""
    stripped = []
    for name in members:
        _, _, rest = name.partition("/")
        if rest:
            stripped.append(rest)
    return stripped


def test_no_canary_survives_the_exclusion(built_sdist):
    """The whole point: a planted file under an excluded path must not ship."""
    leaked = [name for name in built_sdist if CANARY_NAME in name]
    assert not leaked, (
        "sdist exclude rules did not hold; these canaries shipped:\n"
        + "\n".join(leaked)
        + "\n\nFix [tool.hatch.build.targets.sdist] exclude in pyproject.toml."
    )


@pytest.mark.parametrize("excluded", EXCLUDED_DIRS)
def test_excluded_directory_contributes_nothing(built_sdist, excluded):
    """Beyond the canary, no member may live under an excluded directory."""
    prefix = excluded + "/"
    leaked = [name for name in _strip_root(built_sdist) if name.startswith(prefix)]
    assert not leaked, (
        f"{len(leaked)} member(s) shipped from excluded {excluded!r}, e.g.:\n"
        + "\n".join(leaked[:10])
    )


@pytest.mark.parametrize("required", REQUIRED_PATHS)
def test_sdist_still_contains_what_rebuilds_the_project(built_sdist, required):
    """Non-vacuity in the other direction.

    An over-broad exclude would satisfy every assertion above by shipping almost
    nothing. `vscode-extension/src/extension.ts` is listed deliberately: the
    extension SOURCE is tracked and belongs in the sdist even though its
    `node_modules` and build output never do.
    """
    assert required in _strip_root(built_sdist), (
        f"{required!r} is missing from the sdist. An exclude rule is too broad -- "
        f"an sdist must be able to rebuild what the repo builds."
    )
