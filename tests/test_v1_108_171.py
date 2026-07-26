"""jcm#375: context-provider regexes must scan in linear time.

Reported by a Builder-license user on Ubuntu. A single `index_folder` call burned
four minutes at ~100% CPU emitting nothing, and py-spy pinned every sample inside
`ExpressProvider._extract_mounts`, reached through `discover_providers` ->
`_resolve_active_providers`. Provider discovery runs BEFORE file indexing starts,
so the whole index hung with no progress and no log line.

Cause was a leading uncaptured ``(?:\\w+)`` before the ``\\.`` in six patterns
across two providers. It asserts nothing the ``.`` does not already imply, but it
makes the scan quadratic: at every offset inside an unbroken word run the engine
matches the run greedily, fails on the ``.``, then backtracks a character at a
time. One minified chunk or base64 blob in a single source file is enough.

Measured on a lone ``\\w`` run before the fix: 8k chars 0.45s, 32k 7.5s, 128k did
not finish in 120s. After: ~0.00006s at 128k.

The scaling guard below is deliberately empirical rather than a pattern-text
assertion. A future provider can reintroduce the blowup with different syntax,
and only timing catches that.
"""

from __future__ import annotations

import importlib
import pkgutil
import re
import time

import pytest

import jcodemunch_mcp.parser.context as context_pkg


def _all_provider_patterns() -> list[tuple[str, str, re.Pattern]]:
    """Every module-level compiled regex across the context providers."""
    found: list[tuple[str, str, re.Pattern]] = []
    for mod in pkgutil.iter_modules(context_pkg.__path__):
        module = importlib.import_module(
            f"jcodemunch_mcp.parser.context.{mod.name}"
        )
        for name, value in vars(module).items():
            if isinstance(value, re.Pattern):
                found.append((mod.name, name, value))
    return found


def test_the_guard_sees_some_patterns():
    """Non-vacuity: an empty scan would make every check below pass silently."""
    patterns = _all_provider_patterns()
    assert len(patterns) > 10, f"only found {len(patterns)} provider patterns"


def test_no_provider_regex_is_quadratic_on_a_word_run():
    """4x the input must not cost ~16x the time.

    A linear scan costs about 4x. Quadratic backtracking costs about 16x. The
    threshold sits between them, and the absolute floor keeps sub-millisecond
    noise from tripping it.
    """
    small, big = "x" * 4000, "x" * 16000
    offenders = []
    for mod_name, pat_name, pattern in _all_provider_patterns():
        start = time.perf_counter()
        list(pattern.finditer(small))
        small_s = time.perf_counter() - start

        start = time.perf_counter()
        list(pattern.finditer(big))
        big_s = time.perf_counter() - start

        ratio = big_s / small_s if small_s > 1e-9 else 1.0
        if big_s > 0.05 and ratio > 8:
            offenders.append(
                f"{mod_name}.{pat_name}: 4k={small_s:.4f}s 16k={big_s:.4f}s "
                f"({ratio:.1f}x for 4x input)"
            )

    assert not offenders, "quadratic provider regex(es):\n  " + "\n  ".join(offenders)


def test_whole_provider_set_survives_a_minified_blob():
    """The end-to-end property jcm#375 actually violated.

    32k of unbroken word characters, deliberately NOT larger. A regressed
    pattern costs ~7.5s here and trips the assert quickly. At 128k the same
    regression costs over 120 seconds, so the test would hang rather than fail,
    and a guard that hangs CI is a worse guard. 32k is already far past the
    point where quadratic and linear separate by three orders of magnitude.
    """
    blob = "x" * 32_000
    start = time.perf_counter()
    for _mod_name, _pat_name, pattern in _all_provider_patterns():
        list(pattern.finditer(blob))
    elapsed = time.perf_counter() - start
    assert elapsed < 2.0, (
        f"all provider regexes over a 32k word run took {elapsed:.2f}s; "
        "a provider regex has regained quadratic backtracking"
    )


# --- match parity: the fix must not change what is extracted ----------------

_EXPRESS_SOURCE = """
const app = express();
app.get("/users", listUsers);
router.post('/users/:id', createUser);
app.use("/api/users", usersRouter);
router.use( "/v2" , v2Router );
app.use(express.json());
"""

_GO_SOURCE = '''
r := gin.Default()
r.GET("/ping", pingHandler)
r.POST("/users", createUser)
r.Use(authMiddleware)
v1 := r.Group("/api/v1")
'''


def test_express_patterns_still_extract_the_same_routes():
    from jcodemunch_mcp.parser.context.express import (
        _JS_MOUNT,
        _JS_ROUTE,
    )

    routes = [
        (m.group("method").upper(), m.group("path"))
        for m in _JS_ROUTE.finditer(_EXPRESS_SOURCE)
    ]
    assert ("GET", "/users") in routes
    assert ("POST", "/users/:id") in routes

    mounts = [
        (m.group("prefix"), m.group("router"))
        for m in _JS_MOUNT.finditer(_EXPRESS_SOURCE)
    ]
    assert ("/api/users", "usersRouter") in mounts
    # Whitespace around the dot and inside the call still parses.
    assert ("/v2", "v2Router") in mounts


def test_go_patterns_still_extract_the_same_routes():
    from jcodemunch_mcp.parser.context.go_routers import (
        _GO_GROUP,
        _GO_ROUTE,
    )

    routes = [
        (m.group("method"), m.group("path"))
        for m in _GO_ROUTE.finditer(_GO_SOURCE)
    ]
    assert ("GET", "/ping") in routes
    assert ("POST", "/users") in routes

    groups = [m.group("prefix") for m in _GO_GROUP.finditer(_GO_SOURCE)]
    assert "/api/v1" in groups


@pytest.mark.parametrize(
    "module_name,pattern_name",
    [
        ("express", "_JS_ROUTE"),
        ("express", "_JS_MIDDLEWARE"),
        ("express", "_JS_MOUNT"),
        ("go_routers", "_GO_ROUTE"),
        ("go_routers", "_GO_MIDDLEWARE"),
        ("go_routers", "_GO_GROUP"),
    ],
)
def test_the_six_fixed_patterns_have_no_leading_word_run(module_name, pattern_name):
    """Pin the specific six, by pattern text, against a careless revert.

    The timing guard above is the real check. This one names the exact patterns
    so a reverting diff fails with an obvious message instead of a stopwatch.
    """
    module = importlib.import_module(
        f"jcodemunch_mcp.parser.context.{module_name}"
    )
    pattern_text = getattr(module, pattern_name).pattern
    assert not pattern_text.startswith("(?:\\w+)"), (
        f"{module_name}.{pattern_name} regained the leading (?:\\w+) prefix; "
        "see jcm#375 and the note above the pattern"
    )
