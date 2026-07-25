"""Tests for Gleam import extraction and specifier resolution."""

import pytest

from jcodemunch_mcp.parser.imports import (
    _LANGUAGE_EXTRACTORS,
    _gleam_source_roots,
    extract_imports,
    resolve_specifier,
)


GLEAM_SOURCE = """\
import gleam/io
import gleam/option.{type Option, None, Some}
import holmes_msg as msg
import webse/config_types.{type Config, Config}
import cx/commands/vetf/aggregate

pub fn main() {
  io.println("import ignored inside a string")
}
"""


class TestGleamImportExtraction:

    def test_gleam_in_language_extractors(self):
        """Gleam must be registered in _LANGUAGE_EXTRACTORS."""
        assert "gleam" in _LANGUAGE_EXTRACTORS

    def test_extracts_plain_import(self):
        edges = extract_imports(GLEAM_SOURCE, "src/app.gleam", "gleam")
        specifiers = [e["specifier"] for e in edges]
        assert "gleam/io" in specifiers

    def test_extracts_unqualified_names_stripping_type_prefix(self):
        edges = extract_imports(GLEAM_SOURCE, "src/app.gleam", "gleam")
        by_spec = {e["specifier"]: e["names"] for e in edges}
        assert by_spec["gleam/option"] == ["Option", "None", "Some"]
        # `{type Config, Config}` imports the type and the constructor —
        # both surface as the mention name "Config".
        assert by_spec["webse/config_types"] == ["Config", "Config"]

    def test_module_alias_keeps_specifier(self):
        edges = extract_imports(GLEAM_SOURCE, "src/app.gleam", "gleam")
        by_spec = {e["specifier"]: e["names"] for e in edges}
        assert "holmes_msg" in by_spec
        assert by_spec["holmes_msg"] == []

    def test_extracts_deep_module_path(self):
        edges = extract_imports(GLEAM_SOURCE, "src/app.gleam", "gleam")
        specifiers = [e["specifier"] for e in edges]
        assert "cx/commands/vetf/aggregate" in specifiers

    def test_edge_count(self):
        edges = extract_imports(GLEAM_SOURCE, "src/app.gleam", "gleam")
        assert len(edges) == 5

    def test_multiline_unqualified_list(self):
        source = (
            "import holmes_msg.{\n"
            "  type HolmesMsg, type Envelope,\n"
            "  decode_msg,\n"
            "}\n"
        )
        edges = extract_imports(source, "src/app.gleam", "gleam")
        assert len(edges) == 1
        assert edges[0]["specifier"] == "holmes_msg"
        assert edges[0]["names"] == ["HolmesMsg", "Envelope", "decode_msg"]

    def test_unqualified_name_alias(self):
        source = "import gleam/option.{Some as S}\n"
        edges = extract_imports(source, "src/app.gleam", "gleam")
        assert edges[0]["names"] == ["Some"]

    def test_no_false_positives_from_non_import_lines(self):
        source = 'pub fn main() {\n  io.println("import gleam/io")\n}\n'
        edges = extract_imports(source, "src/app.gleam", "gleam")
        assert edges == []


WEVE_STYLE_FILES = {
    "packages/yamlutils/src/yamlutils.gleam",
    "packages/ion/src/ion/ion_yaml.gleam",
    "cells/webse/src/webse/config_yaml.gleam",
    "cells/webse/src/webse/config_types.gleam",
    "cells/webse/test/webse_test.gleam",
    "cells/webse/test/support/helper.gleam",
    "cells/webse/dev/seed_data.gleam",
    "soma/src/soma/config_yaml.gleam",
    "cx/src/cx/cluster_config_yaml.gleam",
    "cells/webse/gleam.toml",
    "packages/yamlutils/gleam.toml",
}


class TestGleamSourceRoots:

    def test_structural_roots_from_gleam_files(self):
        roots = _gleam_source_roots(frozenset(WEVE_STYLE_FILES))
        assert "packages/yamlutils/src" in roots
        assert "cells/webse/src" in roots
        assert "cells/webse/test" in roots
        assert "cells/webse/dev" in roots
        assert "soma/src" in roots
        assert "cx/src" in roots

    def test_gleam_toml_derived_roots(self):
        files = frozenset({"cells/empty/gleam.toml"})
        roots = _gleam_source_roots(files)
        assert "cells/empty/src" in roots
        assert "cells/empty/test" in roots
        assert "cells/empty/dev" in roots

    def test_root_level_package(self):
        files = frozenset({"src/app.gleam", "test/app_test.gleam", "gleam.toml"})
        roots = _gleam_source_roots(files)
        assert "src" in roots
        assert "test" in roots


class TestGleamSpecifierResolution:

    @pytest.mark.parametrize("specifier,importer,expected", [
        # Cross-package import into another package's src root
        ("yamlutils", "packages/ion/src/ion/ion_yaml.gleam",
         "packages/yamlutils/src/yamlutils.gleam"),
        # Same-package import
        ("webse/config_types", "cells/webse/src/webse/config_yaml.gleam",
         "cells/webse/src/webse/config_types.gleam"),
        # Test module importing its package's src module
        ("webse/config_types", "cells/webse/test/webse_test.gleam",
         "cells/webse/src/webse/config_types.gleam"),
        # Test module importing a test-only helper module
        ("support/helper", "cells/webse/test/webse_test.gleam",
         "cells/webse/test/support/helper.gleam"),
        # Dev module importing its package's src module
        ("webse/config_types", "cells/webse/dev/seed_data.gleam",
         "cells/webse/src/webse/config_types.gleam"),
        # Stdlib / hex dependency: unresolvable, no edge
        ("gleam/io", "cells/webse/src/webse/config_yaml.gleam", None),
        ("gleam/list", "soma/src/soma/config_yaml.gleam", None),
    ], ids=[
        "cross_package", "same_package", "test_to_src", "test_helper",
        "dev_to_src", "stdlib_io", "stdlib_list",
    ])
    def test_resolution(self, specifier, importer, expected):
        result = resolve_specifier(specifier, importer, set(WEVE_STYLE_FILES))
        assert result == expected

    def test_non_gleam_importer_unaffected(self):
        """A slash specifier from a non-.gleam importer must not hit gleam roots."""
        result = resolve_specifier(
            "webse/config_types", "scripts/tool.py", set(WEVE_STYLE_FILES)
        )
        assert result is None

    def test_end_to_end_extract_and_resolve(self):
        source = "import webse/config_types.{type Config}\n"
        edges = extract_imports(source, "cells/webse/src/webse/config_yaml.gleam", "gleam")
        assert len(edges) == 1
        resolved = resolve_specifier(
            edges[0]["specifier"],
            "cells/webse/src/webse/config_yaml.gleam",
            set(WEVE_STYLE_FILES),
        )
        assert resolved == "cells/webse/src/webse/config_types.gleam"

    def test_gleam_not_in_missing_extractors(self, tmp_path):
        """After the extractor lands, index_folder must not flag gleam."""
        from jcodemunch_mcp.tools.index_folder import index_folder

        src = tmp_path / "src"
        store = tmp_path / "store"
        src.mkdir()
        store.mkdir()

        (src / "app.gleam").write_text(
            "import gleam/io\n\npub fn main() {\n  io.println(\"hello\")\n}\n"
        )

        r = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
        assert r["success"] is True

        missing = r.get("missing_extractors", [])
        assert "gleam" not in missing, (
            f"gleam should no longer be in missing_extractors: {missing}"
        )
