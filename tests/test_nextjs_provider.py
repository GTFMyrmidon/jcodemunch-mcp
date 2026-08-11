"""Tests for the Next.js context provider."""

from pathlib import Path

import pytest

from jcodemunch_mcp.parser.context.nextjs import (
    NextjsContextProvider,
    _next_route_from_path,
    _next_api_methods_from_content,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_next_project(tmp_path: Path) -> None:
    _write(tmp_path / "next.config.js", "module.exports = {}")
    _write(tmp_path / "package.json", '{"dependencies": {"next": "^14.0.0"}}')


# ---------------------------------------------------------------------------
# Route path conversion
# ---------------------------------------------------------------------------

class TestNextRouteFromPath:
    def test_root_page(self):
        assert _next_route_from_path("app/page.tsx") == "/"

    def test_simple_path(self):
        assert _next_route_from_path("app/users/page.tsx") == "/users"

    def test_dynamic_param(self):
        assert _next_route_from_path("app/users/[id]/page.tsx") == "/users/:id"

    def test_catch_all(self):
        assert _next_route_from_path("app/posts/[...slug]/page.tsx") == "/posts/*"

    def test_route_group_ignored(self):
        """(auth) route group should not appear in URL."""
        assert _next_route_from_path("app/(auth)/login/page.tsx") == "/login"

    def test_nested(self):
        assert _next_route_from_path("app/dashboard/settings/page.tsx") == "/dashboard/settings"

    def test_src_app_prefix(self):
        """src/ directory layout: strip the src/app prefix when supplied."""
        assert _next_route_from_path("src/app/page.tsx", "src/app") == "/"
        assert _next_route_from_path("src/app/users/[id]/page.tsx", "src/app") == "/users/:id"


class TestNextApiMethods:
    def test_get_and_post(self):
        content = """
export async function GET(request: Request) { return Response.json([]); }
export async function POST(request: Request) { return Response.json({}); }
"""
        methods = _next_api_methods_from_content(content)
        assert "GET" in methods
        assert "POST" in methods

    def test_no_exports(self):
        content = "const handler = () => {}; export default handler;"
        methods = _next_api_methods_from_content(content)
        assert methods == []

    def test_sync_function(self):
        content = "export function DELETE(req: Request) { return new Response(); }"
        methods = _next_api_methods_from_content(content)
        assert methods == ["DELETE"]


# ---------------------------------------------------------------------------
# NextjsContextProvider
# ---------------------------------------------------------------------------

class TestNextjsContextProvider:
    def test_detect_next_js(self, tmp_path):
        _make_next_project(tmp_path)
        provider = NextjsContextProvider()
        assert provider.detect(tmp_path) is True

    def test_detect_next_ts(self, tmp_path):
        _write(tmp_path / "next.config.ts", "export default {}")
        provider = NextjsContextProvider()
        assert provider.detect(tmp_path) is True

    def test_detect_next_mjs(self, tmp_path):
        _write(tmp_path / "next.config.mjs", "export default {}")
        provider = NextjsContextProvider()
        assert provider.detect(tmp_path) is True

    def test_detect_no_config(self, tmp_path):
        provider = NextjsContextProvider()
        assert provider.detect(tmp_path) is False

    def test_page_routing(self, tmp_path):
        _make_next_project(tmp_path)
        _write(tmp_path / "app" / "page.tsx", "export default function Home() { return <div/>; }")
        _write(tmp_path / "app" / "users" / "[id]" / "page.tsx",
               "export default function UserPage() { return <div/>; }")

        provider = NextjsContextProvider()
        assert provider.detect(tmp_path)
        provider.load(tmp_path)

        ctx = provider.get_file_context("app/page.tsx")
        assert ctx is not None
        assert "nextjs-page" in ctx.tags
        assert ctx.properties["route"] == "/"

        ctx2 = provider.get_file_context("app/users/[id]/page.tsx")
        assert ctx2 is not None
        assert ":id" in ctx2.properties["route"]

    def test_layout_detected(self, tmp_path):
        _make_next_project(tmp_path)
        _write(tmp_path / "app" / "layout.tsx",
               "export default function RootLayout({ children }) { return <html>{children}</html>; }")

        provider = NextjsContextProvider()
        assert provider.detect(tmp_path)
        provider.load(tmp_path)

        ctx = provider.get_file_context("app/layout.tsx")
        assert ctx is not None
        assert "nextjs-layout" in ctx.tags

    def test_api_route_handler(self, tmp_path):
        _make_next_project(tmp_path)
        _write(tmp_path / "app" / "api" / "users" / "route.ts", """
export async function GET(request: Request) { return Response.json([]); }
export async function POST(request: Request) { return Response.json({}); }
""")

        provider = NextjsContextProvider()
        assert provider.detect(tmp_path)
        provider.load(tmp_path)

        ctx = provider.get_file_context("app/api/users/route.ts")
        assert ctx is not None
        assert "nextjs-api" in ctx.tags
        assert "GET" in ctx.properties["methods"]
        assert "POST" in ctx.properties["methods"]

    def test_middleware_detected(self, tmp_path):
        _make_next_project(tmp_path)
        _write(tmp_path / "middleware.ts", """
export function middleware(request) { return NextResponse.next(); }
export const config = { matcher: ['/dashboard/:path*', '/api/:path*'] };
""")

        provider = NextjsContextProvider()
        assert provider.detect(tmp_path)
        provider.load(tmp_path)

        ctx = provider.get_file_context("middleware.ts")
        assert ctx is not None
        assert "nextjs-middleware" in ctx.tags
        assert "/dashboard/:path*" in ctx.properties.get("matcher", "")

    def test_error_boundary(self, tmp_path):
        _make_next_project(tmp_path)
        _write(tmp_path / "app" / "error.tsx",
               "export default function Error() { return <div>Error</div>; }")

        provider = NextjsContextProvider()
        assert provider.detect(tmp_path)
        provider.load(tmp_path)

        ctx = provider.get_file_context("app/error.tsx")
        assert ctx is not None
        assert "nextjs-error" in ctx.tags

    def test_metadata_has_routes(self, tmp_path):
        _make_next_project(tmp_path)
        _write(tmp_path / "app" / "page.tsx", "export default function Home() { return <div/>; }")
        _write(tmp_path / "app" / "api" / "health" / "route.ts",
               "export function GET() { return Response.json({ok: true}); }")

        provider = NextjsContextProvider()
        assert provider.detect(tmp_path)
        provider.load(tmp_path)

        meta = provider.get_metadata()
        assert "nextjs_routes" in meta
        assert "/" in meta["nextjs_routes"]
        assert "nextjs_api" in meta

    def test_stats(self, tmp_path):
        _make_next_project(tmp_path)
        _write(tmp_path / "app" / "page.tsx", "export default () => <div/>;")
        _write(tmp_path / "app" / "api" / "users" / "route.ts",
               "export function GET() {}")

        provider = NextjsContextProvider()
        assert provider.detect(tmp_path)
        provider.load(tmp_path)

        stats = provider.stats()
        assert stats["page_routes"] >= 1
        assert stats["api_routes"] >= 1

    def test_no_app_dir_is_safe(self, tmp_path):
        _make_next_project(tmp_path)
        provider = NextjsContextProvider()
        assert provider.detect(tmp_path)
        provider.load(tmp_path)
        assert provider.stats()["page_routes"] == 0

    def test_route_group_not_in_url(self, tmp_path):
        _make_next_project(tmp_path)
        _write(tmp_path / "app" / "(marketing)" / "about" / "page.tsx",
               "export default () => <div/>;")

        provider = NextjsContextProvider()
        assert provider.detect(tmp_path)
        provider.load(tmp_path)

        ctx = provider.get_file_context("app/(marketing)/about/page.tsx")
        assert ctx is not None
        assert ctx.properties["route"] == "/about"


# ---------------------------------------------------------------------------
# src/ directory layout (create-next-app --src-dir) — issue #432
# ---------------------------------------------------------------------------

class TestNextjsSrcAppLayout:
    def test_src_app_page_routing(self, tmp_path):
        _make_next_project(tmp_path)
        _write(tmp_path / "src" / "app" / "page.tsx",
               "export default function Home() { return <div/>; }")
        _write(tmp_path / "src" / "app" / "users" / "[id]" / "page.tsx",
               "export default function UserPage() { return <div/>; }")

        provider = NextjsContextProvider()
        assert provider.detect(tmp_path)
        provider.load(tmp_path)

        ctx = provider.get_file_context("src/app/page.tsx")
        assert ctx is not None
        assert "nextjs-page" in ctx.tags
        assert ctx.properties["route"] == "/"

        ctx2 = provider.get_file_context("src/app/users/[id]/page.tsx")
        assert ctx2 is not None
        assert ctx2.properties["route"] == "/users/:id"

        stats = provider.stats()
        assert stats["page_routes"] == 2

    def test_src_app_api_route_handler(self, tmp_path):
        _make_next_project(tmp_path)
        _write(tmp_path / "src" / "app" / "api" / "users" / "route.ts", """
export async function GET(request: Request) { return Response.json([]); }
export async function POST(request: Request) { return Response.json({}); }
""")

        provider = NextjsContextProvider()
        assert provider.detect(tmp_path)
        provider.load(tmp_path)

        ctx = provider.get_file_context("src/app/api/users/route.ts")
        assert ctx is not None
        assert "nextjs-api" in ctx.tags
        assert ctx.properties["endpoint"] == "/api/users"
        assert "GET" in ctx.properties["methods"]
        assert provider.stats()["api_routes"] == 1

    def test_root_app_wins_over_src_app(self, tmp_path):
        """When both layouts exist, Next.js uses root app/ and ignores src/app."""
        _make_next_project(tmp_path)
        _write(tmp_path / "app" / "page.tsx", "export default () => <div/>;")
        _write(tmp_path / "src" / "app" / "other" / "page.tsx", "export default () => <div/>;")

        provider = NextjsContextProvider()
        assert provider.detect(tmp_path)
        provider.load(tmp_path)

        assert provider.get_file_context("app/page.tsx") is not None
        assert provider.get_file_context("src/app/other/page.tsx") is None
        assert provider.stats()["page_routes"] == 1

    def test_root_pages_blocks_src_app(self, tmp_path):
        """A root pages/ directory makes Next.js ignore src/ entirely.

        "src/app or src/pages will be ignored if app or pages are present in
        the root directory" — so a half-finished App Router migration (root
        pages/ still live, src/app staged) must publish NO src/app routes.
        Inventing routes is a worse failure than missing them.
        """
        _make_next_project(tmp_path)
        _write(tmp_path / "pages" / "index.tsx", "export default () => <div/>;")
        _write(tmp_path / "src" / "app" / "page.tsx", "export default () => <div/>;")
        _write(tmp_path / "src" / "app" / "api" / "users" / "route.ts",
               "export function GET() { return Response.json([]); }")

        provider = NextjsContextProvider()
        assert provider.detect(tmp_path)
        provider.load(tmp_path)

        assert provider.get_file_context("src/app/page.tsx") is None
        assert provider.get_file_context("src/app/api/users/route.ts") is None
        assert provider.stats() == {"page_routes": 0, "api_routes": 0}
        assert provider.get_metadata() == {}

    def test_src_middleware_detected(self, tmp_path):
        _make_next_project(tmp_path)
        _write(tmp_path / "src" / "middleware.ts", """
export function middleware(request) { return NextResponse.next(); }
export const config = { matcher: ['/dashboard/:path*'] };
""")

        provider = NextjsContextProvider()
        assert provider.detect(tmp_path)
        provider.load(tmp_path)

        ctx = provider.get_file_context("src/middleware.ts")
        assert ctx is not None
        assert "nextjs-middleware" in ctx.tags
        assert "/dashboard/:path*" in ctx.properties.get("matcher", "")

    def test_root_app_blocks_src_middleware(self, tmp_path):
        """A root app/ directory makes Next.js ignore src/ entirely —
        middleware included. A stale src/middleware.ts must not be published
        as a live handler while a root layout is what actually serves.
        """
        _make_next_project(tmp_path)
        _write(tmp_path / "app" / "page.tsx", "export default () => <div/>;")
        _write(tmp_path / "src" / "middleware.ts", """
export function middleware(request) { return NextResponse.next(); }
export const config = { matcher: ['/dashboard/:path*'] };
""")

        provider = NextjsContextProvider()
        assert provider.detect(tmp_path)
        provider.load(tmp_path)

        assert provider.get_file_context("src/middleware.ts") is None
        # Only the dead middleware is suppressed — the live root app/ tree
        # still publishes.
        assert provider.stats() == {"page_routes": 1, "api_routes": 0}

    def test_root_pages_blocks_src_middleware(self, tmp_path):
        """Same precedence, pages/ shape: root pages/ live, src/ staged."""
        _make_next_project(tmp_path)
        _write(tmp_path / "pages" / "index.tsx", "export default () => <div/>;")
        _write(tmp_path / "src" / "middleware.ts", """
export function middleware(request) { return NextResponse.next(); }
""")

        provider = NextjsContextProvider()
        assert provider.detect(tmp_path)
        provider.load(tmp_path)

        assert provider.get_file_context("src/middleware.ts") is None

    def test_src_app_metadata_and_no_dir_safety(self, tmp_path):
        _make_next_project(tmp_path)
        _write(tmp_path / "src" / "app" / "page.tsx", "export default () => <div/>;")
        _write(tmp_path / "src" / "app" / "api" / "health" / "route.ts",
               "export function GET() { return Response.json({ok: true}); }")

        provider = NextjsContextProvider()
        assert provider.detect(tmp_path)
        provider.load(tmp_path)

        meta = provider.get_metadata()
        assert "/" in meta["nextjs_routes"]
        assert any("/api/health" in key for key in meta["nextjs_api"])

    def test_high_value_paths_cover_both_layouts(self):
        """#433 review: layer_definitions listed the src/ twins but
        high_value_paths only picked up src/app/, so --src-dir projects had
        src/lib and src/components deprioritized by profile consumers."""
        from jcodemunch_mcp.parser.context.framework_profiles import _NEXT
        hv = set(_NEXT.high_value_paths)
        for root in ("app/", "lib/", "components/"):
            assert root in hv, root
            assert f"src/{root}" in hv, f"src/{root}"
