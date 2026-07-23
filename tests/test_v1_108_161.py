# -*- coding: utf-8 -*-
"""v1.108.161 — BM25 tokenizer no longer discards non-ASCII text (jdoc #91 class).

_TOKEN_RE was [a-zA-Z0-9]{2,}, so every non-ASCII character acted as a
separator: CJK symbol names/summaries/docstrings produced zero BM25 tokens
and accented Latin was mangled. Now splits on Unicode word boundaries and
expands CJK runs into overlapping character bigrams — identical at index and
query time, so bigram overlap is the match signal. Suite parity with
jdocmunch-mcp v1.114.1 and jdatamunch-mcp v1.23.1.
"""

from jcodemunch_mcp.tools.search_symbols import _cjk_bigrams, _tokenize


class TestCJKTokenization:
    def test_korean_produces_bigrams(self):
        toks = _tokenize("초과근무 승인 규칙")
        assert toks == ["초과", "과근", "근무", "승인", "규칙"]

    def test_mixed_camelcase_and_korean(self):
        toks = _tokenize("OvertimeService 초과근무 계산")
        assert "overtime" in toks and "service" in toks
        assert "초과" in toks and "근무" in toks and "계산" in toks

    def test_accented_latin_survives_intact(self):
        # Was ['caf', 've'] (single-char fragments dropped by the old {2,}).
        toks = _tokenize("café naïve")
        assert "café" in toks and "naïve" in toks

    def test_japanese_bigrams(self):
        toks = _tokenize("日本語のドキュメント")
        assert "日本" in toks and "本語" in toks

    def test_single_cjk_char_kept(self):
        assert _tokenize("車") == ["車"]

    def test_mixed_script_run_splits(self):
        toks = _tokenize("초과근무OvertimeService")
        assert "overtime" in toks and "초과" in toks

    def test_bigram_helper(self):
        assert _cjk_bigrams("가") == ["가"]
        assert _cjk_bigrams("가나") == ["가나"]
        assert _cjk_bigrams("가나다") == ["가나", "나다"]

    def test_ascii_behavior_unchanged(self):
        # camelCase split + stemming + abbreviation expansion all intact.
        assert _tokenize("getUserData") == ["get", "user", "data"]
        toks = _tokenize("config")
        assert "config" in toks and "configuration" in toks
        # Single-char ASCII tokens still dropped (old {2,} behavior).
        assert _tokenize("a b") == []
