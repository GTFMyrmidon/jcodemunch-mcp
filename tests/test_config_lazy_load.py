"""`config.get()` never answers from `default` for a config it never read (#426).

Before this, `_GLOBAL_CONFIG` was `{}` until `load_config()` ran, so every
env-mapped key resolved to its hardcoded default in an unloaded process:
`JCODEMUNCH_MAX_FILE_SIZE=20000000` read back as 512000, with nothing
distinguishing "the value is 512000" from "I never read the config".

That is filed as a sharp edge rather than a live outage, and the distinction
matters for what these tests are for. No production path was resolving unloaded
-- `index` / `index-file` / `serve` all dispatch after the shared `load_config()`
in `main()`. But `server.py` already carried THREE one-subcommand-at-a-time
`load_config()` patches, each a real bug found separately, and every subcommand
dispatched above the shared call was one edit from being the fourth. The failure
mode is a plausible-looking default, which is the hardest kind to notice.

The subprocess tests below are the load-bearing ones. A fresh interpreter is the
only honest way to reproduce "nothing has been loaded yet" -- inside the suite,
`conftest`'s autouse reset leaves `_GLOBAL_CONFIG` populated with `DEFAULTS`,
which is exactly the state the lazy load must NOT fire for.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from jcodemunch_mcp import config as cfg

SRC = str(Path(__file__).resolve().parents[1] / "src")


def _probe(code: str, env_extra: dict) -> str:
    env = dict(os.environ)
    env["PYTHONPATH"] = SRC
    env["PYTHONIOENCODING"] = "utf-8"
    env.update(env_extra)
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=120
    )
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


class TestTheReportedRepro:
    """A fresh process, an env var set, and no `load_config()` anywhere."""

    @pytest.mark.parametrize(
        "env_var,key,value,hardcoded_default",
        [
            ("JCODEMUNCH_MAX_FILE_SIZE", "max_file_size", "20000000", 512000),
            ("JCODEMUNCH_MAX_FOLDER_FILES", "max_folder_files", "7777", 2000),
            ("JCODEMUNCH_MAX_INDEX_FILES", "max_index_files", "8888", 10000),
            ("JCODEMUNCH_STALENESS_DAYS", "staleness_days", "99", 7),
        ],
    )
    def test_env_var_resolves_without_an_explicit_load(
        self, env_var, key, value, hardcoded_default, tmp_path
    ):
        """Not specific to one key -- the whole env mapping shared the defect."""
        got = _probe(
            "from jcodemunch_mcp import config as c;"
            f"print(c.get({key!r}, {hardcoded_default!r}))",
            {env_var: value, "CODE_INDEX_PATH": str(tmp_path)},
        )
        assert got == value, (
            f"{env_var} was set to {value} and {key} still read back {got}"
        )

    def test_the_issue_body_verbatim(self, tmp_path):
        """Through `get_max_file_size()`, which is how #425's harness hit it."""
        got = _probe(
            "from jcodemunch_mcp.security import get_max_file_size;"
            "print(get_max_file_size())",
            {"JCODEMUNCH_MAX_FILE_SIZE": "20000000", "CODE_INDEX_PATH": str(tmp_path)},
        )
        assert got == "20000000"

    def test_env_mapping_actually_covers_the_probed_keys(self):
        """Non-vacuity floor: if these keys ever leave `ENV_VAR_MAPPING`, the
        parametrized cases above would pass by resolving nothing at all."""
        mapped = set(cfg.ENV_VAR_MAPPING.values())
        for key in ("max_file_size", "max_folder_files", "max_index_files", "staleness_days"):
            assert key in mapped, f"{key} is no longer env-mapped; retarget this file"


class TestAReadWritesNothing:
    """The one new side effect a lazy load could smuggle in.

    `load_config()` auto-creates a default `config.jsonc` when none exists. A
    READ that creates a file in the user's storage directory would be a worse
    surprise than the defect being fixed, so the lazy path passes
    `create_missing=False`.
    """

    def test_reading_a_key_creates_no_config_file(self, tmp_path):
        probe = (
            "import os;"
            "from jcodemunch_mcp import config as c;"
            "c.get('max_folder_files', 2000);"
            "print(os.path.exists(os.path.join(os.environ['CODE_INDEX_PATH'], 'config.jsonc')))"
        )
        assert _probe(probe, {"CODE_INDEX_PATH": str(tmp_path)}) == "False"

    def test_an_explicit_load_still_creates_it(self, tmp_path):
        """Control: the auto-create is not broken, only declined on the read path."""
        probe = (
            "import os;"
            "from jcodemunch_mcp import config as c;"
            "c.load_config();"
            "print(os.path.exists(os.path.join(os.environ['CODE_INDEX_PATH'], 'config.jsonc')))"
        )
        assert _probe(probe, {"CODE_INDEX_PATH": str(tmp_path)}) == "True"

    def test_create_missing_false_still_resolves_the_env(self, tmp_path):
        """The suppressed file is the template and the template is DEFAULTS, so
        declining to write it must not change a single resolved value."""
        probe = (
            "from jcodemunch_mcp import config as c;"
            "c.load_config(create_missing=False);"
            "print(c.get('max_folder_files', 2000))"
        )
        assert _probe(
            probe, {"CODE_INDEX_PATH": str(tmp_path), "JCODEMUNCH_MAX_FOLDER_FILES": "4321"}
        ) == "4321"


class TestItDoesNotFireWhenItShouldNot:
    """The lazy load is gated on not-loaded AND empty. Both halves matter."""

    def test_a_hand_populated_config_is_not_clobbered(self, monkeypatch):
        """Every test that stuffs a dict into `_GLOBAL_CONFIG` does this. A load
        triggered on the flag alone would overwrite all of them."""
        monkeypatch.setattr(cfg, "_CONFIG_LOADED", False)
        monkeypatch.setattr(cfg, "_GLOBAL_CONFIG", {"max_folder_files": 12345})

        def _boom(*a, **k):
            raise AssertionError("lazy load fired over a populated config")

        monkeypatch.setattr(cfg, "load_config", _boom)
        assert cfg.get("max_folder_files", 2000) == 12345

    def test_an_already_loaded_config_does_not_reload(self, monkeypatch):
        calls = []
        monkeypatch.setattr(cfg, "_CONFIG_LOADED", True)
        monkeypatch.setattr(cfg, "_GLOBAL_CONFIG", {})
        monkeypatch.setattr(cfg, "load_config", lambda *a, **k: calls.append(1))
        cfg.get("max_folder_files", 2000)
        cfg.get("max_index_files", 10000)
        assert calls == []

    def test_it_loads_once_not_once_per_key(self, monkeypatch):
        """Emptiness alone as the trigger would re-read the file on every key
        for any state that legitimately resolves to `{}`."""
        calls = []

        def _fake_load(*a, **k):
            calls.append(1)
            cfg._CONFIG_LOADED = True

        monkeypatch.setattr(cfg, "_CONFIG_LOADED", False)
        monkeypatch.setattr(cfg, "_GLOBAL_CONFIG", {})
        monkeypatch.setattr(cfg, "load_config", _fake_load)
        for _ in range(5):
            cfg.get("max_folder_files", 2000)
        assert calls == [1]


class TestItNeverRaises:
    """A read that starts failing is a worse defect than the silent default."""

    def test_a_failing_load_falls_through_to_the_default(self, monkeypatch):
        def _boom(*a, **k):
            raise OSError("storage is unreadable")

        monkeypatch.setattr(cfg, "_CONFIG_LOADED", False)
        monkeypatch.setattr(cfg, "_GLOBAL_CONFIG", {})
        monkeypatch.setattr(cfg, "load_config", _boom)
        assert cfg.get("max_folder_files", 2000) == 2000

    def test_a_failing_load_does_not_mark_the_config_loaded(self, monkeypatch):
        monkeypatch.setattr(cfg, "_CONFIG_LOADED", False)
        monkeypatch.setattr(cfg, "_GLOBAL_CONFIG", {})
        monkeypatch.setattr(cfg, "load_config", lambda *a, **k: (_ for _ in ()).throw(OSError()))
        cfg.get("max_folder_files", 2000)
        assert cfg._CONFIG_LOADED is False, "a failed load must stay retryable"


class TestTheExplicitCallsStillRun:
    """Concern 1 from the issue: `main()` re-runs `load_config()` AFTER
    `_setup_logging()` on purpose, so config warnings reach the configured log
    destination. A lazy load that fired first must not consume that diagnostic.

    It cannot, because only the LAZY path is conditional -- every explicit
    `load_config()` call is unconditional and re-emits. This pins that shape.
    """

    def test_load_config_itself_is_not_gated_on_the_flag(self):
        import inspect
        src = inspect.getsource(cfg.load_config)
        assert "if _CONFIG_LOADED" not in src, (
            "load_config must stay unconditional; gating it would silence the "
            "post-logging re-run in main()"
        )

    def test_main_still_reloads_after_setup_logging(self):
        import inspect
        from jcodemunch_mcp import server as S
        src = inspect.getsource(S.main)
        assert src.index("_setup_logging(args)") < src.rindex("config_module.load_config()"), (
            "the post-logging reload is what makes config warnings visible"
        )


class TestFlagLifecycle:
    def test_a_real_load_sets_the_flag(self, tmp_path):
        probe = (
            "from jcodemunch_mcp import config as c;"
            "before = c._CONFIG_LOADED;"
            "c.load_config();"
            "print(before, c._CONFIG_LOADED)"
        )
        assert _probe(probe, {"CODE_INDEX_PATH": str(tmp_path)}) == "False True"

    def test_a_lazy_load_sets_the_flag(self, tmp_path):
        probe = (
            "from jcodemunch_mcp import config as c;"
            "c.get('max_folder_files', 2000);"
            "print(c._CONFIG_LOADED)"
        )
        assert _probe(probe, {"CODE_INDEX_PATH": str(tmp_path)}) == "True"
