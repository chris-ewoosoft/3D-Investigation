"""The toolchain that decides whether the gates are green is pinned.

A gate is a *measurement*, and a measurement whose instrument floats is not a
measurement. ``ruff`` decides whether the code is clean, ``mypy`` decides
whether the types hold, ``coverage`` decides whether the floor is met: an
unpinned release of any of them can change a verdict — green or red — for
reasons that have nothing to do with the code under test, and nobody reviews
that change because it was never a change to the repository.

This module is the tripwire that keeps the toolchain pinned (ADR 0012). It
reads the *files*, not the running package, so a violation is reported as a
fact about a file:

* every requirement of ``requirements.txt`` is an exact pin — the single
  documented exception is ``torch``, whose CUDA and CPU builds share a version
  number;
* the ONNX Runtime the export contract is validated against is the version the
  Qt application ships (``ONNXRUNTIME_VERSION`` in ``CMakeLists.txt``);
* every requirement of ``requirements-test.txt`` is an exact pin, and the
  hash-pinned lock resolves each of them *with* its digest;
* the CI workflow installs that lock with ``--require-hashes`` and never
  installs a floating package.

A *rename* has the same effect as an unpinned tool — the guard quietly stops
checking anything — so the presence of the files is checked too.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "AIComputerVision"
REQUIREMENTS = PACKAGE / "requirements.txt"
TEST_REQUIREMENTS = PACKAGE / "requirements-test.txt"
TEST_LOCK = PACKAGE / "requirements-test.lock"
CMAKE = ROOT / "CMakeLists.txt"
GATE_WORKFLOW = ROOT / ".github" / "workflows" / "Build-cvtrain-ci.yml"
NIGHTLY_WORKFLOW = ROOT / ".github" / "workflows" / "cvtrain-nightly.yml"

#: The one requirement allowed to be a range, and why.
#:
#: ``torch`` publishes the CUDA and the CPU build under the *same* version
#: number and selects them through the index the package is installed from —
#: so an exact pin cannot express "the CUDA build on Linux, the CPU build on
#: the nightly runner" without pinning the local variant too. The range is
#: still bounded (``>=2.7,<3``): a major framework jump is not accepted
#: silently.
RANGE_EXCEPTIONS = {"torch"}

_SPEC = re.compile(r"^([A-Za-z0-9_.\-]+)([=<>!~].*)$")
_LOCK_ENTRY = re.compile(r"^([A-Za-z0-9_.\-]+)==([^\s;\\]+)")
_HASH = re.compile(r"--hash=sha256:([0-9a-f]{64})")


def _requirement_lines(path: Path) -> list[str]:
    """The requirement lines of a requirements file (comments dropped)."""

    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _split_spec(line: str) -> tuple[str, str]:
    """``"torch>=2.7,<3"`` -> ``("torch", ">=2.7,<3")``."""

    match = _SPEC.match(line)
    if match is None:
        raise AssertionError(f"not a requirement: {line!r}")
    return match.group(1).lower().replace("_", "-"), match.group(2)


def _lock_entries(path: Path) -> dict[str, tuple[str, set[str]]]:
    """``{name: (version, digests)}`` as declared by a pip lock file."""

    entries: dict[str, tuple[str, set[str]]] = {}
    name = ""
    version = ""
    digests: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _LOCK_ENTRY.match(line)
        if match:
            if name:
                entries[name] = (version, digests)
            name = match.group(1).lower().replace("_", "-")
            version = match.group(2)
            digests = set()
        digests.update(_HASH.findall(line))
    if name:
        entries[name] = (version, digests)
    return entries


def _install_lines(text: str) -> list[str]:
    """The ``pip install`` lines of a workflow file."""

    return [line.strip() for line in text.splitlines() if "pip install" in line]


def _named_packages(line: str) -> list[str]:
    """The package names an install line names directly (options dropped)."""

    packages: list[str] = []
    tokens = line.split()
    # Drop the command itself: "pip install ..." / "python -m pip install ...".
    while tokens and tokens[0] in {"pip", "python", "-m", "install"}:
        tokens.pop(0)
    for part in tokens:
        if part.startswith("-"):
            continue
        if part == "pip":
            continue
        if "/" in part or part.endswith(".txt") or part.endswith(".lock"):
            continue
        packages.append(part)
    return packages


@unittest.skipUnless(REQUIREMENTS.exists(), f"requirements.txt not found at {REQUIREMENTS}")
class RuntimeRequirementsTests(unittest.TestCase):
    """The training stack is version-pinned."""

    def test_every_requirement_is_exactly_pinned(self) -> None:
        for line in _requirement_lines(REQUIREMENTS):
            name, spec = _split_spec(line)
            if name in RANGE_EXCEPTIONS:
                self.assertNotEqual(spec, "", f"{name} must state a bounded range")
                self.assertIn("<", spec, f"{name} must be bounded on the upper side, got {spec!r}")
                continue
            self.assertTrue(
                spec.startswith("=="),
                f"{name} is not exactly pinned ({spec!r}); an unpinned requirement lets a new "
                "release change what the pipeline produces without a review",
            )

    def test_the_contract_engine_is_the_one_the_application_ships(self) -> None:
        # The export contract is validated against ONNX Runtime, so the version
        # that validates an artifact has to be the version that loads it. The
        # Qt application bundles the runtime CMakeLists.txt pins.
        if not CMAKE.exists():
            self.skipTest("Python-only checkout: no CMakeLists.txt to compare against")
        match = re.search(r'set\(ONNXRUNTIME_VERSION\s+"([^"]+)"', CMAKE.read_text(encoding="utf-8"))
        self.assertIsNotNone(match, "ONNXRUNTIME_VERSION not found in CMakeLists.txt")
        assert match is not None
        expected = match.group(1)
        for line in _requirement_lines(REQUIREMENTS):
            name, spec = _split_spec(line)
            if name == "onnxruntime":
                self.assertEqual(spec, f"=={expected}", "the export contract must be validated against the shipped runtime")
                return
        self.fail("onnxruntime is not listed in requirements.txt")


@unittest.skipUnless(TEST_REQUIREMENTS.exists(), f"requirements-test.txt not found at {TEST_REQUIREMENTS}")
class GateToolchainTests(unittest.TestCase):
    """The tools that decide green/red are pinned and hash-verified."""

    def test_every_gate_requirement_is_exactly_pinned(self) -> None:
        for line in _requirement_lines(TEST_REQUIREMENTS):
            name, spec = _split_spec(line)
            self.assertTrue(
                spec.startswith("=="),
                f"{name} is not exactly pinned ({spec!r}); a gate that floats is a gate that can change its verdict",
            )

    def test_the_lock_resolves_every_requirement_with_its_digest(self) -> None:
        if not TEST_LOCK.exists():
            self.fail(f"{TEST_LOCK} is missing; CI installs the lock, so a missing lock is a broken build")
        entries = _lock_entries(TEST_LOCK)
        for line in _requirement_lines(TEST_REQUIREMENTS):
            name, spec = _split_spec(line)
            self.assertIn(name, entries, f"{name} is not in the lock")
            version, digests = entries[name]
            self.assertEqual(spec, f"=={version}", f"{name} resolves to {version} in the lock, not {spec}")
            self.assertTrue(digests, f"{name} carries no --hash=sha256 digest; --require-hashes would refuse it")

    def test_the_lock_pins_nothing_loosely(self) -> None:
        entries = _lock_entries(TEST_LOCK)
        for name, (version, digests) in entries.items():
            self.assertTrue(version, f"{name} has no version in the lock")
            self.assertTrue(digests, f"{name} carries no digest")


@unittest.skipUnless(GATE_WORKFLOW.exists(), f"{GATE_WORKFLOW} not found")
class GateWorkflowTests(unittest.TestCase):
    """CI installs the gate toolchain from the hash-pinned lock."""

    def _installs(self) -> list[str]:
        return [line for line in _install_lines(GATE_WORKFLOW.read_text(encoding="utf-8")) if "--upgrade" not in line]

    def test_requirements_files_are_installed_with_hash_verification(self) -> None:
        # *Every* install from a requirements file has to be hash-verified — not
        # just one of them: a second job that installs the same lock without
        # ``--require-hashes`` is exactly how an unpinned tool sneaks back in.
        for line in self._installs():
            if "-r " in line or "--requirement" in line:
                self.assertIn("--require-hashes", line, f"a requirements file is installed without hash verification: {line}")
        self.assertTrue(
            any("requirements-test.lock" in line for line in self._installs()),
            "the gate toolchain must come from the hash-pinned lock",
        )

    def test_no_floating_package_is_installed(self) -> None:
        for line in self._installs():
            if "-r " in line or "--requirement" in line:
                continue
            for package in _named_packages(line):
                self.assertIn("==", package, f"{package} is installed without a version: {line}")


@unittest.skipUnless(NIGHTLY_WORKFLOW.exists(), f"{NIGHTLY_WORKFLOW} not found")
class NightlyWorkflowTests(unittest.TestCase):
    """The real-dependency job installs a pinned stack too."""

    def test_no_floating_package_is_installed(self) -> None:
        for line in _install_lines(NIGHTLY_WORKFLOW.read_text(encoding="utf-8")):
            if "--upgrade" in line:
                continue
            for package in _named_packages(line):
                self.assertIn("==", package, f"{package} is installed without a version: {line}")


class ToolchainFilePresenceTests(unittest.TestCase):
    """A moved file must not switch the guard off silently.

    The tests above skip themselves when the file they read is absent, which
    is right for a Python-only checkout — but a *rename* has the identical
    effect, leaving CI green with nothing being checked. This test inverts
    the guard: when the package is checked out, the files the tripwires read
    have to be there too.
    """

    def test_the_guarded_files_are_present_when_the_package_is(self) -> None:
        if not PACKAGE.exists():
            self.skipTest("Python-only checkout: there is no toolchain to pin")
        self.assertTrue(REQUIREMENTS.exists(), f"{REQUIREMENTS} moved; the runtime pin tripwire is inactive")
        self.assertTrue(TEST_REQUIREMENTS.exists(), f"{TEST_REQUIREMENTS} moved; the gate pin tripwire is inactive")
        self.assertTrue(TEST_LOCK.exists(), f"{TEST_LOCK} moved; CI would install nothing")
        if (ROOT / ".github" / "workflows").exists():
            self.assertTrue(GATE_WORKFLOW.exists(), f"{GATE_WORKFLOW} moved; the toolchain is installed unpinned again")
            self.assertTrue(NIGHTLY_WORKFLOW.exists(), f"{NIGHTLY_WORKFLOW} moved; the real-dependency check is gone")


if __name__ == "__main__":
    unittest.main()
