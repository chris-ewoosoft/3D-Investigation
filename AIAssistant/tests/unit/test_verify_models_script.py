"""Tests for the model pin pre-flight script (ADR 0023).

The script reuses the loader's ``_matches_pin``, so these cases pin the CLI
contract around it: a matching file reports OK and exits 0, a corrupted file
names the artifact and exits 1 (fail closed), and a missing file is reported
but only fatal under ``--require-all``.
"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "verify_models.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("verify_models", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class VerifyModelsScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.script = _load_script()

    def _workspace(self, directory: Path, content: bytes, pinned: bytes | None = None):
        """A tiny registry: one model file and the sha256 it is pinned to."""
        models_dir = directory / "Models"
        models_dir.mkdir()
        (models_dir / "tiny.gguf").write_bytes(content)
        config_dir = directory / "config"
        config_dir.mkdir()
        sha = hashlib.sha256(pinned if pinned is not None else content).hexdigest()
        (config_dir / "models.toml").write_text(
            "[[models]]\n"
            'repo_id = "eval/tiny"\n'
            'filename = "tiny.gguf"\n'
            'desc = "tiny fixture"\n'
            f'sha256 = "{sha}"\n',
            encoding="utf-8",
        )
        return models_dir, config_dir

    def _run(self, models_dir: Path, config_dir: Path, *extra: str):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = self.script.main([
                "--models-dir", str(models_dir),
                "--config-dir", str(config_dir),
                *extra,
            ])
        return code, out.getvalue(), err.getvalue()

    def test_a_matching_artifact_reports_ok(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            models_dir, config_dir = self._workspace(Path(directory), b"pinned weights")
            code, out, err = self._run(models_dir, config_dir)
        self.assertEqual(code, 0)
        self.assertIn("OK        tiny.gguf (model)", out)
        self.assertEqual(err, "")

    def test_a_corrupted_artifact_fails_closed_and_is_named(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            models_dir, config_dir = self._workspace(
                Path(directory), b"tampered weights", pinned=b"pinned weights")
            code, out, err = self._run(models_dir, config_dir)
        self.assertEqual(code, 1)
        self.assertIn("MISMATCH  tiny.gguf (model)", out)
        self.assertIn("do not match the pinned SHA-256", err)

    def test_a_missing_artifact_passes_unless_require_all(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            models_dir, config_dir = self._workspace(Path(directory), b"pinned weights")
            (models_dir / "tiny.gguf").unlink()
            code, out, _ = self._run(models_dir, config_dir)
            strict_code, _, strict_err = self._run(models_dir, config_dir, "--require-all")
        self.assertEqual(code, 0)
        self.assertIn("MISSING   tiny.gguf (model)", out)
        self.assertEqual(strict_code, 1)
        self.assertIn("--require-all", strict_err)


if __name__ == "__main__":
    unittest.main()
