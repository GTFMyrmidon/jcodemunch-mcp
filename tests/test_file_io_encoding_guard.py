"""Text-mode file IO declares its encoding (v1.108.264).

Read side of the cp1252 hazard. `open()`, `Path.read_text()` and
`Path.write_text()` use the platform default when no encoding is given, which is
cp1252 on Windows. Reading a UTF-8 file then raises on the five bytes cp1252
leaves undefined (`81 8D 8F 90 9D`) and silently mangles everything else it can
map. Writing produces a file the rest of the world cannot read.

This is the third member of one family:
  - v1.108.230  subprocess INPUT   -> tests/test_subprocess_encoding_guard.py
  - v1.108.262  our own OUTPUT     -> tests/test_cli_output_encoding.py
  - v1.108.264  file IO, this file

⚠⚠ The scanner is POSITIONAL-AWARE, and that is the whole reason this file
exists in this shape. `Path.read_text(encoding=None, errors=None)` takes encoding
as its FIRST POSITIONAL parameter, so `read_text("utf-8", errors="replace")` is
already correct. A keyword-only check counted 28 correct call sites as broken and
produced a published figure of "45 unencoded sites" that was wrong by a factor of
three. `test_positional_encoding_is_recognized` pins that, because the
false-positive direction is what discredits a guard: a ratchet nobody believes
gets exemptions added to it.
"""

import ast
import pathlib

import pytest

SRC = pathlib.Path(__file__).resolve().parents[1] / "src"

IO_FUNCS = {"open", "read_text", "write_text"}

# Callers that have no encoding parameter at all. Named individually, with the
# reason, rather than skipping a directory and hoping.
NO_ENCODING_PARAM = {
    "os": "os.open returns a file descriptor; encoding does not apply",
    "zipfile": "ZipFile.open is always binary",
}

# Ratchet. Empty on purpose: an entry here is a KNOWN gap with a reason, not a
# parking space. `test_known_gap_does_not_rot` deletes stale entries.
KNOWN_UNENCODED: set = set()


def _owner(node: ast.Call) -> str:
    fn = node.func
    if isinstance(fn, ast.Attribute) and fn.value is not None:
        try:
            return ast.unparse(fn.value)
        except Exception:
            return ""
    return ""


def _func_name(node: ast.Call) -> str:
    fn = node.func
    if isinstance(fn, ast.Attribute):
        return fn.attr
    if isinstance(fn, ast.Name):
        return fn.id
    return ""


def offenders_in_source(source: str, label: str) -> list[str]:
    """Text-mode IO calls that do not declare an encoding, positional or keyword."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _func_name(node)
        if name not in IO_FUNCS:
            continue
        kwargs = {k.arg for k in node.keywords if k.arg}
        if "encoding" in kwargs:
            continue
        # POSITIONAL encoding. read_text(enc, errs) / write_text(data, enc, errs)
        if name == "read_text" and len(node.args) >= 1:
            continue
        if name == "write_text" and len(node.args) >= 2:
            continue
        if name == "open":
            owner = _owner(node)
            if any(owner == k or owner.startswith(k + ".") for k in NO_ENCODING_PARAM):
                continue
            if owner and owner.lower().startswith(("zf", "zip", "archive")):
                continue
            mode = None
            if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                mode = node.args[1].value
            if "mode" in kwargs and not mode:
                continue  # computed mode; cannot prove text
            if mode and "b" in str(mode):
                continue
        out.append(f"{label}:{node.lineno}")
    return out


def _all_offenders() -> list[str]:
    found = []
    for path in sorted(SRC.rglob("*.py")):
        try:
            src = path.read_text(encoding="utf-8")
        except OSError:
            continue
        found.extend(offenders_in_source(src, path.relative_to(SRC).as_posix()))
    return found


class TestTheRatchet:
    def test_no_unencoded_text_io(self):
        offenders = [o for o in _all_offenders() if o not in KNOWN_UNENCODED]
        assert not offenders, (
            "text-mode file IO with no encoding= reads/writes with the platform "
            "default (cp1252 on Windows): it RAISES on the bytes cp1252 leaves "
            'undefined and silently mangles the rest. Add encoding="utf-8", '
            'errors="replace":\n  ' + "\n  ".join(offenders)
        )

    def test_known_gap_does_not_rot(self):
        """A listed gap that is actually fixed must be removed, so the set
        cannot decay into a permanent exemption."""
        stale = KNOWN_UNENCODED - set(_all_offenders())
        assert not stale, (
            "KNOWN_UNENCODED lists call sites that are now compliant; delete "
            "them: " + ", ".join(sorted(stale))
        )

    def test_the_tree_is_actually_being_scanned(self):
        """Non-vacuity floor: a scanner that silently walks nothing passes
        every assertion above."""
        files = list(SRC.rglob("*.py"))
        assert len(files) > 100, f"only {len(files)} files found; scan is broken"


class TestTheScannerItself:
    """⚠⚠ A guard is only worth having if its FALSE POSITIVE rate is zero.

    The first version of this scan checked only the `encoding=` keyword. It
    reported 45 offenders where 14 existed, because it did not know that
    `read_text`'s first positional parameter IS encoding. That number reached a
    published changelog and release notes before anyone checked it.
    """

    @pytest.mark.parametrize("snippet", [
        'p.read_text()',
        'p.write_text(data)',
        'open(path)',
        'open(path, "r")',
        'open(path, "w")',
        'with open(path) as f: pass',
    ])
    def test_it_flags_the_real_thing(self, snippet):
        assert offenders_in_source(snippet, "x"), f"missed: {snippet}"

    @pytest.mark.parametrize("snippet", [
        'p.read_text(encoding="utf-8")',
        'p.read_text("utf-8")',
        'p.read_text("utf-8", errors="replace")',
        'p.write_text(data, encoding="utf-8")',
        'p.write_text(data, "utf-8")',
        'open(path, encoding="utf-8")',
        'open(path, "rb")',
        'open(path, "wb")',
        'os.open(path, os.O_RDWR)',
        'zf.open(name)',
    ])
    def test_it_does_not_flag_correct_code(self, snippet):
        assert not offenders_in_source(snippet, "x"), f"false positive: {snippet}"

    def test_positional_encoding_is_recognized(self):
        """The exact bug that produced the wrong published figure. Pinned so a
        future edit to this scanner cannot reintroduce it."""
        assert not offenders_in_source('p.read_text("utf-8", errors="replace")', "x")
        import inspect
        # Unbound method, so parameters[0] is `self`; encoding is the first
        # parameter a CALLER supplies, which is what the scanner counts.
        sig = inspect.signature(pathlib.Path.read_text)
        params = [p for p in sig.parameters if p != "self"]
        assert params[0] == "encoding", (
            "read_text's first parameter is no longer encoding; this scanner's "
            "positional rule is wrong and must be re-derived"
        )


class TestTheHazardIsReal:
    """Without these, the guard above is a style rule rather than a bug fix."""

    def test_the_default_decode_raises_on_utf8_content(self, tmp_path):
        p = tmp_path / "t.txt"
        p.write_bytes("a ” b".encode("utf-8"))  # U+201D -> E2 80 9D, 9D undefined
        try:
            p.read_text()
        except UnicodeDecodeError as e:
            assert e.encoding in ("charmap", "cp1252")
        else:
            pytest.skip("this platform's default encoding can read UTF-8")

    def test_explicit_utf8_round_trips(self, tmp_path):
        p = tmp_path / "t.txt"
        original = "café — test ”"
        p.write_text(original, encoding="utf-8")
        assert p.read_text(encoding="utf-8", errors="replace") == original


class TestLegacyFilesStillLoad:
    """A file previously written as cp1252 must not become a crash.

    `tuning.jsonc`'s header comment carries an em-dash, so on Windows it was
    being written as cp1252 and read back as cp1252 -- self-consistent, and
    wrong for every other reader. Reading it as UTF-8 now mangles that one
    character, which lands inside a COMMENT that `_strip_jsonc` removes.
    """

    def test_a_cp1252_tuning_file_still_parses(self, tmp_path):
        import json
        from jcodemunch_mcp.retrieval import tuning
        p = tmp_path / "tuning.jsonc"
        p.write_bytes('// risk — comment\n{"a": 1}\n'.encode("cp1252"))
        raw = tuning._strip_jsonc(p.read_text(encoding="utf-8", errors="replace"))
        assert json.loads(raw) == {"a": 1}
