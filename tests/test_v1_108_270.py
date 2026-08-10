"""v1.108.270 — a directory that declares itself a cache is not corpus.

The Cache Directory Tagging Specification (https://bford.info/cachedir/): a
directory is a cache when it holds a ``CACHEDIR.TAG`` whose first 43 bytes are
``Signature: 8a477f597d28d172789f06886806bc55``.

⚠ This arrived from OUTSIDE, and the route matters. A sibling tool wrote a
derived projection into a directory inside an indexed tree; jcodemunch walked in
and indexed its JSON as source, so private content came back from
``search_symbols``. The tool then adopted ``CACHEDIR.TAG`` to declare the
directory derived — and jcodemunch ignored the declaration, because it had no
notion of one.

⚠⚠ **Why a tag and not another entry in the denylist.** Three fixes were
available and two of them are traps:

- ``_SKIP_DIRECTORY_NAMES`` already holds ``.git``, ``.venv``, ``.tox`` — every
  dotted directory somebody thought of in advance. Adding the offender re-arms
  the same trap for the next tool.
- jdocmunch fixed its half with a dotted-directory RULE (jdoc#113), which is
  better, but keys on a naming convention and cannot see a cache that is not
  dotted.
- The tag is a declaration by the WRITER. It is the only one of the three that
  does not require every reader to know about every writer in advance, and it
  covers non-dotted caches.

⚠⚠ **The signature check is the invariant; the filename is one instance of it.**
A name-only check is exactly the mistake that produced this whole family: the
sibling tool's own test asserted its sidecar suffix was ``.txt`` (one instance of
"inert extension") and stayed green while a ``.json`` beside it was ingested;
v1.108.267 keyed a constant branch on node type alone and it read as coverage
while returning None for every Kotlin input. Tests below pin the signature, and
a name-only implementation fails them.
"""

import inspect

import pytest

# ⚠ Imported inside the helpers, not at module scope. None of these symbols
# exist before this release, and a module-level import turns every assertion in
# this file into ONE collection error — which reports as a single red line and
# proves nothing about which guards actually bite (lesson from #429's baseline).
VALID = b"Signature: 8a477f597d28d172789f06886806bc55"


def is_cache_directory(path):
    from jcodemunch_mcp.security import is_cache_directory as impl

    return impl(path)


def _tag(d, body=VALID):
    d.mkdir(parents=True, exist_ok=True)
    (d / "CACHEDIR.TAG").write_bytes(body)
    return d


# ── the predicate ───────────────────────────────────────────────────────────


class TestIsCacheDirectory:
    def test_the_signature_is_the_spec_signature(self):
        """43 bytes, verbatim. A typo here silently stops honouring the tag."""
        from jcodemunch_mcp.security import (
            CACHEDIR_TAG_FILENAME,
            CACHEDIR_TAG_SIGNATURE,
        )

        assert CACHEDIR_TAG_SIGNATURE == VALID
        assert len(CACHEDIR_TAG_SIGNATURE) == 43
        assert CACHEDIR_TAG_FILENAME == "CACHEDIR.TAG"

    def test_a_valid_tag_is_recognised(self, tmp_path):
        assert is_cache_directory(_tag(tmp_path / "c")) is True

    def test_trailing_content_after_the_signature_is_allowed(self, tmp_path):
        """The spec requires the first 43 bytes; real tags carry a comment
        block after them, so anything stricter rejects every genuine tag."""
        body = VALID + b"\n# Created by some tool.\n# See https://bford.info/cachedir/\n"
        assert is_cache_directory(_tag(tmp_path / "c", body)) is True

    def test_an_untagged_directory_is_not_a_cache(self, tmp_path):
        (tmp_path / "plain").mkdir()
        assert is_cache_directory(tmp_path / "plain") is False

    @pytest.mark.parametrize(
        "body,label",
        [
            (b"", "empty file"),
            (b"Signature: 0000000000000000000000000000000", "wrong hash"),
            (b"# a comment first\n" + VALID, "signature not first"),
            (VALID[:-1], "truncated by one byte"),
            (b"signature: 8a477f597d28d172789f06886806bc55", "wrong case"),
        ],
    )
    def test_a_file_named_right_but_wrong_inside_is_NOT_a_cache(
        self, tmp_path, body, label
    ):
        """⚠⚠ THE control for this release. Every one of these has a file
        named CACHEDIR.TAG, so a name-only implementation passes them all and
        silently excludes directories nobody declared. Deleting these cases
        deletes the only thing separating the invariant from the instance."""
        assert is_cache_directory(_tag(tmp_path / label, body)) is False

    def test_an_unreadable_or_absent_tag_fails_CLOSED_to_not_a_cache(self, tmp_path):
        """A permission error must never be read as 'this is a cache' — that
        would empty a corpus on an IO fault and report success."""
        assert is_cache_directory(tmp_path / "does-not-exist-at-all") is False

    def test_a_directory_named_CACHEDIR_TAG_is_not_a_tag(self, tmp_path):
        """The tag must be a FILE. A directory by that name makes open() fail,
        which the fail-closed path must turn into False, not an exception."""
        (tmp_path / "c" / "CACHEDIR.TAG").mkdir(parents=True)
        assert is_cache_directory(tmp_path / "c") is False


# ── the walk honours it ─────────────────────────────────────────────────────


def _project(tmp_path, tagged="cache"):
    (tmp_path / "src").mkdir(parents=True)
    (tmp_path / "src" / "app.py").write_text("def real_function():\n    return 1\n")
    c = _tag(tmp_path / tagged)
    (c / "derived.json").write_text('{"private": "derived data"}')
    (c / "nested").mkdir()
    (c / "nested" / "deep.py").write_text("def buried():\n    return 2\n")
    return tmp_path


class TestTheWalkPrunesTaggedDirectories:
    def _walk(self, root):
        from jcodemunch_mcp.tools.index_folder import discover_local_files

        files, _, counts = discover_local_files(root.resolve())
        return {f.relative_to(root.resolve()).as_posix() for f in files}, counts

    def test_a_tagged_directory_is_pruned(self, tmp_path):
        got, counts = self._walk(_project(tmp_path))
        assert got == {"src/app.py"}
        assert counts["cache_dir"] == 1

    def test_it_prunes_the_whole_subtree_not_just_the_top(self, tmp_path):
        """Pruning at the directory level must stop descent — a source file
        buried two levels down is the case a per-file filter would miss."""
        got, _ = self._walk(_project(tmp_path))
        assert not any("nested" in p for p in got), got

    def test_a_NON_dotted_cache_is_covered(self, tmp_path):
        """The reason this beats a dot-dir rule. `build_output/` is not dotted
        and jdoc's is_skipped_dot_dir would never see it."""
        got, counts = self._walk(_project(tmp_path, tagged="build_output"))
        assert got == {"src/app.py"}
        assert counts["cache_dir"] == 1

    def test_a_lookalike_directory_is_still_indexed(self, tmp_path):
        """End-to-end form of the signature control: a CACHEDIR.TAG with the
        wrong bytes must NOT cost the caller their source files."""
        root = tmp_path
        (root / "src").mkdir(parents=True)
        (root / "src" / "app.py").write_text("def real_function():\n    return 1\n")
        fake = _tag(root / "notacache", b"Signature: deadbeef")
        (fake / "keep_me.py").write_text("def keep_me():\n    return 3\n")
        got, counts = self._walk(root)
        assert got == {"src/app.py", "notacache/keep_me.py"}
        assert counts["cache_dir"] == 0

    def test_a_clean_project_is_unchanged(self, tmp_path):
        (tmp_path / "a.py").write_text("def a():\n    return 1\n")
        got, counts = self._walk(tmp_path)
        assert got == {"a.py"}
        assert counts["cache_dir"] == 0


# ── the opt-out ─────────────────────────────────────────────────────────────


class TestOptOut:
    def _cap(self, monkeypatch, value):
        from jcodemunch_mcp import config as config_module

        monkeypatch.setitem(
            config_module._GLOBAL_CONFIG, "respect_cachedir_tag", value
        )

    def test_config_false_re_admits_the_cache(self, tmp_path, monkeypatch):
        from jcodemunch_mcp.tools.index_folder import discover_local_files

        self._cap(monkeypatch, False)
        root = _project(tmp_path).resolve()
        files, _, counts = discover_local_files(root)
        got = {f.relative_to(root).as_posix() for f in files}
        assert "cache/derived.json" in got
        assert counts["cache_dir"] == 0

    @pytest.mark.parametrize("value", [True, "yes", 1, None, "garbage"])
    def test_only_an_explicit_FALSE_disables_it(self, value, monkeypatch):
        """⚠ A typo must not silently re-admit cache trees. Anything that is
        not exactly False keeps the standard honoured."""
        from jcodemunch_mcp import config as config_module
        from jcodemunch_mcp.security import get_respect_cachedir_tag

        monkeypatch.setitem(
            config_module._GLOBAL_CONFIG, "respect_cachedir_tag", value
        )
        assert get_respect_cachedir_tag() is True

    def test_the_env_var_is_registered_like_its_siblings(self):
        from jcodemunch_mcp.config import ENV_VAR_MAPPING

        assert ENV_VAR_MAPPING["JCODEMUNCH_RESPECT_CACHEDIR_TAG"] == "respect_cachedir_tag"

    def test_the_default_is_ON(self):
        from jcodemunch_mcp.config import DEFAULTS

        assert DEFAULTS["respect_cachedir_tag"] is True


# ── the reason is an exclusion, not a withholding ───────────────────────────


class TestCacheDirIsNotWithheld:
    def test_cache_dir_does_not_block_absence_claims(self):
        """⚠ Deliberate and load-bearing. `too_large` / `file_limit` /
        `unreadable` are WITHHELD: the file is real, current, wanted, and only
        our limit kept it out, so a zero-result cannot prove absence. A tagged
        directory is derived data BY THE WRITER'S OWN DECLARATION, which makes
        it corpus definition like `gitignore` — the corpus is smaller on
        purpose and absence over it stays citable."""
        from jcodemunch_mcp.tools.index_folder import WITHHELD_SKIP_REASONS

        assert "cache_dir" not in WITHHELD_SKIP_REASONS

    def test_coverage_stays_complete_when_only_a_cache_was_pruned(self, tmp_path):
        from jcodemunch_mcp.tools.index_folder import _coverage_report

        report = _coverage_report({"cache_dir": 1}, files_indexed=1, no_symbols_count=0)
        assert report.get("complete") is not False


# ── every discovery entry point, or none of them (#429's lesson) ────────────


class TestAllEntryPoints:
    def test_the_fast_path_checks_ancestors(self, tmp_path):
        """The watcher path never walks, so a directory-level prune cannot
        reach it. Without this branch a file created inside a tagged cache
        enters the index by the back door on the next edit."""
        from jcodemunch_mcp.tools.index_folder import (
            _build_index_filters,
            _build_skip_dirs_regex,
            _should_index_file,
        )

        root = _project(tmp_path).resolve()
        cfg = _build_index_filters(
            root=root,
            skip_dirs_regex=_build_skip_dirs_regex(),
            check_binary=False,
            respect_cachedir_tag=True,
        )
        ok, reason, _, _ = _should_index_file(root / "cache" / "nested" / "deep.py", cfg)
        assert ok is False and reason == "cache_dir"

        ok, _, _, _ = _should_index_file(root / "src" / "app.py", cfg)
        assert ok is True

    def test_the_fast_path_honours_the_opt_out_too(self, tmp_path):
        from jcodemunch_mcp.tools.index_folder import (
            _build_index_filters,
            _build_skip_dirs_regex,
            _should_index_file,
        )

        root = _project(tmp_path).resolve()
        cfg = _build_index_filters(
            root=root,
            skip_dirs_regex=_build_skip_dirs_regex(),
            check_binary=False,
            respect_cachedir_tag=False,
        )
        ok, _, _, _ = _should_index_file(root / "cache" / "nested" / "deep.py", cfg)
        assert ok is True

    def test_explicit_paths_deliberately_BYPASS_the_tag(self, tmp_path):
        """⚠⚠ Not an oversight, and asserted so nobody 'fixes' it. The
        explicit-paths route already opts past gitignore and skip-directory
        rules by design, so a caller can name a generated file on purpose; the
        security filters (symlink, secret, binary, size) are what it keeps.
        `cache_dir` is corpus definition, so it belongs on the bypass side. A
        caller naming a file inside a cache is asking for it by name."""
        from jcodemunch_mcp.tools.index_folder import resolve_explicit_paths

        root = _project(tmp_path).resolve()
        files, _, _, _ = resolve_explicit_paths(
            root, ["cache/nested/deep.py"], max_files=100
        )
        assert [f.name for f in files] == ["deep.py"]

    def test_a_directory_argument_still_gets_the_rule(self, tmp_path):
        """The bypass is for a NAMED file. Handing the same route a directory
        recurses through the walk, which does apply the rule — same as
        gitignore behaves there."""
        from jcodemunch_mcp.tools.index_folder import resolve_explicit_paths

        root = _project(tmp_path).resolve()
        files, _, _, _ = resolve_explicit_paths(root, ["."], max_files=100)
        assert [f.relative_to(root).as_posix() for f in files] == ["src/app.py"]


# ── the limit, stated (index_repo) ──────────────────────────────────────────


class TestGitHubPathIsHonestlyUncovered:
    def test_index_repo_does_not_claim_tag_support(self):
        """⚠ NOT covered on the GitHub path, deliberately. Validating the
        signature needs the blob's CONTENT, and the tree listing carries only
        paths and sizes — so honouring it there costs a fetch per candidate
        directory. A filename-only check is the one thing this release exists
        to reject, so the GitHub walk gets nothing rather than a lookalike.

        This test pins the absence so it stays a known gap rather than being
        discovered later as a silent inconsistency."""
        from jcodemunch_mcp.tools import index_repo as mod

        src = inspect.getsource(mod.discover_source_files)
        assert "CACHEDIR" not in src
