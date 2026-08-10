"""Nuxt 4 srcDir support (#434) and the JS-extension gap in profiles (#435).

Nuxt 4 changed the DEFAULT srcDir to `app/`, so a stock Nuxt 4 project kept
pages at `app/pages/` while the provider probed only root `pages/`. Framework
detected, zero routes, no warning.

⚠ A fixture that only ever builds the Nuxt 3 root layout cannot fail on this
class, which is why every pre-existing nuxt test passed throughout.
"""
from pathlib import Path

import pytest


def _write(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _nuxt_project(root: Path, config: str = "export default defineNuxtConfig({})") -> None:
    _write(root / "nuxt.config.ts", config)


def _provider():
    from jcodemunch_mcp.parser.context.nuxt import NuxtContextProvider
    return NuxtContextProvider()


# ---------------------------------------------------------------------------
# srcDir resolution
# ---------------------------------------------------------------------------

class TestResolveSrcDir:
    def test_nuxt3_root_layout_resolves_to_root(self, tmp_path):
        _nuxt_project(tmp_path)
        _write(tmp_path / "pages" / "index.vue", "<template/>")
        assert _provider()._resolve_src_dir(tmp_path) == ""

    def test_nuxt4_app_layout_resolves_to_app(self, tmp_path):
        _nuxt_project(tmp_path)
        _write(tmp_path / "app" / "pages" / "index.vue", "<template/>")
        assert _provider()._resolve_src_dir(tmp_path) == "app"

    def test_explicit_srcdir_in_config_wins(self, tmp_path):
        """Config is the actual answer; the probe is only a fallback."""
        _nuxt_project(tmp_path, "export default defineNuxtConfig({ srcDir: 'src/' })")
        _write(tmp_path / "app" / "pages" / "index.vue", "<template/>")
        _write(tmp_path / "src" / "pages" / "index.vue", "<template/>")
        assert _provider()._resolve_src_dir(tmp_path) == "src"

    @pytest.mark.parametrize("raw,expected", [
        ("'app'", "app"), ('"app/"', "app"), ("'./src'", "src"), ("'src/'", "src"),
    ])
    def test_srcdir_value_is_normalised(self, tmp_path, raw, expected):
        _nuxt_project(tmp_path, f"export default defineNuxtConfig({{ srcDir: {raw} }})")
        assert _provider()._resolve_src_dir(tmp_path) == expected

    def test_unrelated_app_dir_does_not_hijack_the_layout(self, tmp_path):
        """An `app/` with no Nuxt-shaped child must not relocate the parse.

        Guessing wrong here moves the whole scan, so the probe requires a
        recognised child rather than merely the directory existing.
        """
        _nuxt_project(tmp_path)
        _write(tmp_path / "app" / "notes.txt", "unrelated")
        _write(tmp_path / "pages" / "index.vue", "<template/>")
        assert _provider()._resolve_src_dir(tmp_path) == ""


# ---------------------------------------------------------------------------
# The defect: stock Nuxt 4 indexed zero routes
# ---------------------------------------------------------------------------

class TestNuxt4DefaultLayout:
    def test_app_pages_produce_routes(self, tmp_path):
        _nuxt_project(tmp_path)
        _write(tmp_path / "app" / "pages" / "index.vue", "<template/>")
        _write(tmp_path / "app" / "pages" / "users" / "[id].vue", "<template/>")

        p = _provider()
        assert p.detect(tmp_path)
        p.load(tmp_path)

        assert p.stats()["page_routes"] == 2
        ctx = p.get_file_context("app/pages/index.vue")
        assert ctx is not None and ctx.properties["route"] == "/"
        assert p.get_file_context("app/pages/users/[id].vue").properties["route"] == "/users/:id"

    def test_app_composables_produce_auto_imports(self, tmp_path):
        """The knock-on half: an empty map returns early and kills every edge."""
        _nuxt_project(tmp_path)
        _write(tmp_path / "app" / "pages" / "index.vue", "<template/>")
        _write(tmp_path / "app" / "composables" / "useAuth.ts", "export function useAuth() {}")
        _write(tmp_path / "app" / "utils" / "fmt.ts", "export function fmt() {}")

        p = _provider()
        assert p.detect(tmp_path)
        p.load(tmp_path)
        assert set(p._auto_import_symbols) == {"useAuth", "fmt"}

    def test_server_api_stays_at_root_under_app_layout(self, tmp_path):
        """`server/` does NOT move under app/ in Nuxt 4. Root must still win."""
        _nuxt_project(tmp_path)
        _write(tmp_path / "app" / "pages" / "index.vue", "<template/>")
        _write(tmp_path / "server" / "api" / "health.ts", "export default defineEventHandler(() => {})")

        p = _provider()
        assert p.detect(tmp_path)
        p.load(tmp_path)
        assert p.stats()["api_routes"] == 1
        assert p.get_file_context("server/api/health.ts") is not None

    def test_nested_server_api_is_an_additive_fallback(self, tmp_path):
        """Only consulted when root server/api is absent, so Nuxt 4 is unaffected."""
        _nuxt_project(tmp_path, "export default defineNuxtConfig({ srcDir: 'src' })")
        _write(tmp_path / "src" / "pages" / "index.vue", "<template/>")
        _write(tmp_path / "src" / "server" / "api" / "ping.ts",
               "export default defineEventHandler(() => {})")

        p = _provider()
        assert p.detect(tmp_path)
        p.load(tmp_path)
        assert p.stats()["api_routes"] == 1


class TestNuxt3StillWorks:
    """Controls. These pass before AND after; the root layout must not move."""

    def test_root_pages_unchanged(self, tmp_path):
        _nuxt_project(tmp_path)
        _write(tmp_path / "pages" / "index.vue", "<template/>")
        _write(tmp_path / "pages" / "blog" / "[slug].vue", "<template/>")

        p = _provider()
        assert p.detect(tmp_path)
        p.load(tmp_path)
        assert p.stats()["page_routes"] == 2
        assert p.get_file_context("pages/index.vue").properties["route"] == "/"
        assert p.get_file_context("pages/blog/[slug].vue").properties["route"] == "/blog/:slug"

    def test_root_composables_unchanged(self, tmp_path):
        _nuxt_project(tmp_path)
        _write(tmp_path / "composables" / "useThing.ts", "export function useThing() {}")
        p = _provider()
        assert p.detect(tmp_path)
        p.load(tmp_path)
        assert "useThing" in p._auto_import_symbols


# ---------------------------------------------------------------------------
# #435: profile patterns
# ---------------------------------------------------------------------------

class TestProfilePatterns:
    def test_nuxt_profile_covers_both_layouts(self, tmp_path):
        from jcodemunch_mcp.parser.context.framework_profiles import _NUXT
        pats = _NUXT.entry_point_patterns
        assert any(p.startswith("app/pages/") for p in pats)
        assert any(p.startswith("pages/") for p in pats)
        # server/ stays at the root in both layouts, so it must NOT be mirrored.
        assert not any(p.startswith("app/server/") for p in pats)

    def test_nuxt_layers_cover_both_layouts(self):
        from jcodemunch_mcp.parser.context.framework_profiles import _NUXT
        by_name = {layer.name: layer.paths for layer in _NUXT.layer_definitions}
        for name in ("pages", "components", "composables", "stores", "plugins"):
            assert any(p.startswith("app/") for p in by_name[name]), name
        assert by_name["server"] == ["server/"]

    @pytest.mark.parametrize("profile_name", ["nuxt", "nestjs"])
    def test_ts_patterns_carry_js_variants(self, profile_name):
        """A TS-only entry-point list gives a JS project no reachability seed.

        `next` is exempt BY NAME until PR #433 lands, because that PR edits the
        same lines and we will not force a conflict onto a contributor's rebase.
        Remove the exemption when #433 merges; #435 tracks it.
        """
        from jcodemunch_mcp.parser.context import framework_profiles as fp
        profile = {"nuxt": fp._NUXT, "nestjs": fp._NESTJS}[profile_name]
        globs = list(profile.entry_point_patterns)
        for layer in profile.layer_definitions:
            globs += layer.paths
        offenders = [
            g for g in globs
            if (g.endswith(".ts") or g.endswith(".tsx"))
            and "{" not in g
        ]
        assert not offenders, f"{profile_name} has TS-only globs: {offenders}"
