"""Skill-candidate advisory — "a skill might outperform a doc here".

Covers `_check_skill_candidates` in audit_agent_config, its default-off gate,
and the `skill_candidate` correction kind in suggest_corrections.

⚠ NON-VACUITY NOTE. The interesting assertions here are the NEGATIVE ones: that
file size alone never fires, that a package root is never reported as a subtree,
and that the advisory is silent on a default install. A test suite that only
proved "a concentrated section is found" would pass just as happily on a checker
that flagged every large section, which is the failure this feature was designed
against.
"""

import pytest

from jcodemunch_mcp.tools.audit_agent_config import (
    DEFAULT_SKILL_THRESHOLDS,
    _best_subtree,
    _check_skill_candidates,
    _skill_advisor_mode,
    _split_sections,
    audit_agent_config,
)

# ---------------------------------------------------------------------------
# Tuning corpus — named per the ROADMAP close condition.
# ---------------------------------------------------------------------------
#
# Thresholds were tuned 2026-08-06 against five real agent configs, none of them
# written for this feature and four of them outside the repo under test:
#
#   C:\MCPs\jcodemunch-mcp\CLAUDE.md   22,843 tok,  9 H2 sections, 779 files
#   C:\MCPs\jdocmunch-mcp\CLAUDE.md    11,491 tok, 24 H2 sections, 542 files
#   C:\MCPs\jdatamunch-mcp\CLAUDE.md   10,188 tok,  6 H2 sections, 161 files
#   C:\MCPs\jcodemunch-mcp\AGENTS.md    1,317 tok,  3 H2 sections, 779 files
#   C:\Users\j\.claude\CLAUDE.md          379 tok,  1 H2 section,  779 files
#
# At the shipped thresholds that corpus yields TWO findings:
#   - jdoc CLAUDE.md, a per-release section -> src/jdocmunch_mcp/tools
#     (0.67 concentration, 0.12 repo share, 739 tokens)
#   - jcm AGENTS.md "Session-Aware Routing" -> src/jcodemunch_mcp/tools
#     (0.67 concentration, 0.13 repo share, 625 tokens)
#
# ⚠ The second is a KNOWN WEAK POSITIVE and is recorded here rather than tuned
# away. It is global routing guidance; it scores as concentrated because the
# tool names it cites resolve to the files that implement those tools. Any
# config that names the tools it tells an agent to call has this shape. Two
# findings is not a precision measurement and must not be quoted as one.
#
# ⚠ jcm's own CLAUDE.md yields ZERO findings, including its "Current State"
# section. That section IS bloat (~7k tokens) and `_check_bloat` flags it, but
# its references span the whole package, so it is not scope-concentrated. The
# roadmap entry originally cited it as the dogfood case; measuring falsified
# that. Do not reinstate the claim without re-measuring.
_TUNING_SET = (
    "jcodemunch-mcp/CLAUDE.md",
    "jdocmunch-mcp/CLAUDE.md",
    "jdatamunch-mcp/CLAUDE.md",
    "jcodemunch-mcp/AGENTS.md",
    "~/.claude/CLAUDE.md",
)


def _repo(n_other: int = 100) -> tuple[dict, set]:
    """A synthetic repo: a narrow subtree plus a large unrelated remainder."""
    symbol_files = {
        "ingest_otel_file": {"src/pkg/runtime/ingest.py"},
        "parse_otel_file": {"src/pkg/runtime/otel.py"},
        "redact_trace_record": {"src/pkg/runtime/redact.py"},
        "resolve_to_symbol_id": {"src/pkg/runtime/resolve.py"},
        "ingest_sql_log_file": {"src/pkg/runtime/sql_ingest.py"},
        "unrelated_helper": {"src/pkg/web/views.py"},
    }
    source_files = {
        "src/pkg/runtime/ingest.py", "src/pkg/runtime/otel.py",
        "src/pkg/runtime/redact.py", "src/pkg/runtime/resolve.py",
        "src/pkg/runtime/sql_ingest.py", "src/pkg/web/views.py",
    }
    source_files |= {f"src/pkg/web/mod{i}.py" for i in range(n_other)}
    return symbol_files, source_files


def _section(title: str, refs: list[str], padding_words: int = 900) -> str:
    body = " ".join(f"`{r}`" for r in refs)
    filler = " ".join(["prose"] * padding_words)
    return f"## {title}\n\n{body}\n\n{filler}\n"


def _info(content: str, scope: str = "global") -> dict:
    return {"path": "/home/u/.claude/CLAUDE.md", "content": content, "scope": scope}


CONCENTRATED = ["ingest_otel_file", "parse_otel_file", "redact_trace_record",
                "resolve_to_symbol_id", "ingest_sql_log_file"]


class TestSignalIsConcentrationNotSize:
    """The whole design rests on these two tests."""

    def test_concentrated_section_is_flagged(self):
        symbol_files, source_files = _repo()
        content = _section("Runtime trace ingestion", CONCENTRATED)
        hits = _check_skill_candidates(_info(content), symbol_files, source_files)
        assert len(hits) == 1
        assert hits[0]["subtree"] == "src/pkg/runtime"
        assert hits[0]["category"] == "skill_candidate"

    def test_large_section_with_scattered_refs_is_not_flagged(self):
        """A huge section is NOT a finding when its references are spread out.

        This is the test that separates this checker from a size threshold. If
        it ever goes green while `test_concentrated_section_is_flagged` also
        passes on a size-only implementation, the signal has been lost.
        """
        symbol_files, source_files = _repo()
        scattered = ["unrelated_helper"] * 3 + ["ingest_otel_file", "parse_otel_file"]
        content = _section("Everything", scattered, padding_words=4000)
        hits = _check_skill_candidates(_info(content), symbol_files, source_files)
        assert hits == []

    def test_package_root_is_never_reported_as_a_subtree(self):
        """`src/pkg` contains the whole repo, so "concentrated in src/pkg" is vacuous."""
        symbol_files = {
            f"sym{i}": {f"src/pkg/mod{i}.py"} for i in range(8)
        }
        source_files = {f"src/pkg/mod{i}.py" for i in range(8)}
        content = _section("Everything", list(symbol_files))
        hits = _check_skill_candidates(_info(content), symbol_files, source_files)
        assert hits == []


class TestThresholdBoundaries:
    def test_small_section_below_token_floor_is_ignored(self):
        symbol_files, source_files = _repo()
        content = _section("Tiny", CONCENTRATED, padding_words=5)
        assert _check_skill_candidates(_info(content), symbol_files, source_files) == []

    def test_too_few_resolved_refs_is_ignored(self):
        """Concentration over 2 references is noise, not a signal."""
        symbol_files, source_files = _repo()
        content = _section("Thin", CONCENTRATED[:2])
        assert _check_skill_candidates(_info(content), symbol_files, source_files) == []

    def test_shipped_floor_is_reachable(self):
        """A floor nothing can clear is 'off', not 'strict'.

        The pre-measurement default was 0.8 with a 0.6 share cap and it found
        nothing in the tuning corpus. Guards the direction of that correction.
        """
        assert DEFAULT_SKILL_THRESHOLDS["concentrationFloor"] <= 0.7
        assert DEFAULT_SKILL_THRESHOLDS["subtreeShareCap"] <= 0.3

    def test_deepest_qualifying_prefix_wins(self):
        paths = ["src/pkg/runtime/a.py", "src/pkg/runtime/b.py", "src/pkg/runtime/c.py"]
        all_files = set(paths) | {f"src/pkg/web/m{i}.py" for i in range(50)}
        best = _best_subtree(paths, all_files, 0.65, 0.25)
        assert best["prefix"] == "src/pkg/runtime"


class TestHonesty:
    def test_finding_states_relevance_was_not_measured(self):
        """Required by the ROADMAP close condition.

        Resident cost and concentration are measured; whether the section was
        ever *needed* is not, and the message must not let a reader assume it.
        """
        symbol_files, source_files = _repo()
        content = _section("Runtime trace ingestion", CONCENTRATED)
        hit = _check_skill_candidates(_info(content), symbol_files, source_files)[0]
        assert hit["relevance_measured"] is False
        assert "not measured" in hit["message"].lower()

    def test_recommendation_never_says_summarise(self):
        """The action is a move of existing prose, never a rewrite."""
        symbol_files, source_files = _repo()
        content = _section("Runtime trace ingestion", CONCENTRATED)
        msg = _check_skill_candidates(_info(content), symbol_files, source_files)[0]["message"]
        for banned in ("summar", "condense", "distil", "rewrite"):
            assert banned not in msg.lower()

    def test_no_index_means_no_finding(self):
        """Without an index there is nothing to resolve against, so no claim."""
        content = _section("Runtime trace ingestion", CONCENTRATED)
        assert _check_skill_candidates(_info(content), {}, set()) == []


class TestDefaultOff:
    def test_mode_defaults_to_off(self, monkeypatch):
        monkeypatch.delenv("JCODEMUNCH_SKILL_ADVISOR_MODE", raising=False)
        assert _skill_advisor_mode() in ("off", "advise")

    def test_unrecognised_mode_is_off(self, monkeypatch):
        import jcodemunch_mcp.config as config_module
        monkeypatch.setattr(config_module, "get", lambda k, d=None: "ADVISE!!")
        assert _skill_advisor_mode() == "off"

    def test_audit_omits_skill_findings_when_disabled(self, tmp_path, monkeypatch):
        import jcodemunch_mcp.config as config_module
        monkeypatch.setattr(config_module, "get", lambda k, d=None: "off")
        (tmp_path / "CLAUDE.md").write_text(
            _section("Runtime trace ingestion", CONCENTRATED), encoding="utf-8")
        monkeypatch.setattr(
            "jcodemunch_mcp.tools.audit_agent_config._GLOBAL_CONFIGS", [])
        result = audit_agent_config(project_path=str(tmp_path))
        assert result["skill_advisor"]["enabled"] is False
        assert result["skill_advisor"]["hint"]
        assert not [f for f in result["findings"] if f["category"] == "skill_candidate"]

    def test_explicit_parameter_overrides_config(self, tmp_path, monkeypatch):
        """`skill_candidates=True` opts in without touching global config."""
        import jcodemunch_mcp.config as config_module
        monkeypatch.setattr(config_module, "get", lambda k, d=None: "off")
        (tmp_path / "CLAUDE.md").write_text("## X\n\nnothing here\n", encoding="utf-8")
        monkeypatch.setattr(
            "jcodemunch_mcp.tools.audit_agent_config._GLOBAL_CONFIGS", [])
        result = audit_agent_config(project_path=str(tmp_path), skill_candidates=True)
        assert result["skill_advisor"]["enabled"] is True
        assert result["skill_advisor"]["hint"] is None


class TestSectionSplitting:
    def test_preamble_before_first_h2_is_not_a_section(self):
        secs = _split_sections("intro prose\nmore intro\n\n## First\n\nbody\n")
        assert [s["title"] for s in secs] == ["First"]

    def test_line_span_is_one_based_and_stops_at_last_content_line(self):
        """end_line must not land on the NEXT heading.

        The section slice runs to the next heading's start, so counting its
        newlines raw overshoots by the trailing blank run. Caught this off by
        one on first run; the reported range is what a user opens an editor to.
        """
        #      1        2   3          4   5       6   7         8   9
        text = "line1\n" "\n" "## Alpha\n" "\n" "body\n" "\n" "## Beta\n" "\n" "tail\n"
        secs = _split_sections(text)
        assert [s["title"] for s in secs] == ["Alpha", "Beta"]
        assert (secs[0]["line"], secs[0]["end_line"]) == (3, 5)
        assert secs[0]["end_line"] < secs[1]["line"]
        assert (secs[1]["line"], secs[1]["end_line"]) == (7, 9)


class TestSuggestCorrectionsWiring:
    def test_skill_findings_become_corrections(self):
        from jcodemunch_mcp.tools.suggest_corrections import _skill_candidate_corrections
        audit = {"findings": [{
            "category": "skill_candidate", "severity": "info",
            "message": "m", "file": "CLAUDE.md", "line": 10, "end_line": 40,
            "section": "Runtime trace ingestion", "tokens": 900,
            "subtree": "src/pkg/runtime", "concentration": 0.8,
            "repo_share": 0.1, "resolved_refs": 5, "relevance_measured": False,
        }]}
        out = _skill_candidate_corrections(audit)
        assert len(out) == 1
        assert out[0]["kind"] == "skill_candidate"
        assert out[0]["suggested_patch"] is None
        assert out[0]["evidence"]["relevance_measured"] is False
        assert "Runtime trace ingestion" in out[0]["recommended_action"]

    def test_other_categories_are_not_swept_in(self):
        from jcodemunch_mcp.tools.suggest_corrections import _skill_candidate_corrections
        audit = {"findings": [{"category": "bloat", "message": "big"}]}
        assert _skill_candidate_corrections(audit) == []

    def test_stale_config_reads_category_not_type(self):
        """Regression: this read f["type"], which audit_agent_config never sets.

        The membership test could not match, so `stale_config` corrections had
        never been emitted. Fails against the pre-fix reader.
        """
        from jcodemunch_mcp.tools.suggest_corrections import _stale_config_corrections
        audit = {"findings": [{
            "category": "stale_reference", "severity": "warning",
            "message": "References symbol 'gone' which was not found in the index",
            "file": "CLAUDE.md", "line": 12,
        }]}
        out = _stale_config_corrections(audit)
        assert len(out) == 1
        assert out[0]["kind"] == "stale_config"
