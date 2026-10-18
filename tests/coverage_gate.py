"""Line-coverage gate for the C++ guard layer (measured with OpenCppCoverage).

The C++ side of the repository had no coverage measurement at all: the
architecture guard, the mutation check and the CTest suite proved behaviour,
but nothing prevented an untested branch from being added to the files that
every plugin depends on. This script runs the owning tests of the guard layer
under OpenCppCoverage, merges the per-test cobertura reports and enforces the
per-file floors below.

Scope: translation-unit sources (``.cpp``) of the kernel/guard layer. Header
only templates (``ServiceRegistry.h``, ``MailThreadGuard.h``) compile inline
into their callers under ``/O2 /Ob2`` and cannot be mapped line-by-line by the
tool, so they are pinned by ``tests/mutation_check.py`` instead (null
registration, GUI-thread warning) — see tests/README.md for the split.

The floors are a ratchet, not an aspiration: they equal the measured value of
the current suite (rounded down), so any new uncovered line in these files
fails the gate. Raise a floor when its file reaches the next integer.

Usage (from the repository root, PATH prepared like the CTest step):

    python tests/coverage_gate.py            # enforce the floors
    python tests/coverage_gate.py --measure  # just print the numbers

CI runs the enforcing form right after the CTest step in Build-C++-ci.yml
(OpenCppCoverage is installed by the workflow there; locally the tool is
discovered through OPENCPPCOVERAGE_EXE, PATH or the known install paths).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent

# One row per test executable that owns part of the gate scope.
# (ctest name, exe file name, extra environment)
TESTS: tuple[tuple[str, str, dict[str, str]], ...] = (
    ("TestServiceRegistry", "TestServiceRegistry.exe", {}),
    ("TestPluginDependencyResolver", "TestPluginDependencyResolver.exe", {}),
    ("TestPluginHost", "TestPluginHost.exe", {}),
    # Loads the real plugin DLLs through the default QPluginLoader path —
    # the only test covering PluginHost::loadWithQPluginLoader.
    ("TestPluginSmoke", "TestPluginSmoke.exe", {}),
    ("TestAppShell", "TestAppShell.exe", {"QT_QPA_PLATFORM": "offscreen"}),
    ("TestUserManagerCrypto", "TestUserManagerCrypto.exe", {}),
    ("TestMailCredentialLifecycle", "TestMailCredentialLifecycle.exe", {}),
    ("TestChatSessionManager", "TestChatSessionManager.exe", {}),
    ("TestSettingsService", "TestSettingsService.exe", {}),
    ("TestShellService", "TestShellService.exe", {}),
    ("TestLanguageManager", "TestLanguageManager.exe", {}),
)

# Measured floors: repository-relative path -> minimum line coverage in %.
# A file listed here must appear in the merged report; the measurement mode
# prints candidates for files that are not listed yet. The number next to each
# entry is the floor; the comment records the measurement it was ratcheted
# from (2026-10, MSVC 2022 / VS 17.12, Release). Raise a floor when its file
# reaches the next integer — never lower one to make a change pass.
FLOORS: dict[str, float] = {
    # Kernel + guard layer: the files every plugin transitively depends on.
    "src/core/pluginhost.cpp": 90,               # measured 91.8
    "src/core/plugindependencyresolver.cpp": 92,  # measured 93.3
    "src/core/secretvault.cpp": 92,               # measured 92.9
    "src/core/usermanager.cpp": 57,               # measured 57.5
    "src/core/languagemanager.cpp": 90,           # measured 91.8
    "src/core/appconfig.cpp": 36,                 # measured 37.1 (path matrix)
    # Composition root + service layer the wiring test owns end-to-end.
    "src/app/appshell.cpp": 100,                  # measured 100
    "src/services/ai/chatsessionmanager.cpp": 100,  # measured 100
    "src/services/ai/chatsessionstore.cpp": 100,    # measured 100
    "src/services/settings/settingsservice.cpp": 100,  # measured 100
    "src/services/shell/shellservice.cpp": 95,    # measured 96.3
}

_TOOL_CANDIDATES = (
    Path(os.environ.get("LOCALAPPDATA", "")) / "Tools/OpenCppCoverage/app/OpenCppCoverage.exe",
    Path("C:/Program Files/OpenCppCoverage/OpenCppCoverage.exe"),
    Path("C:/Program Files (x86)/OpenCppCoverage/OpenCppCoverage.exe"),
    BASE / "tools/OpenCppCoverage/OpenCppCoverage.exe",
)


def find_tool() -> Path:
    override = os.environ.get("OPENCPPCOVERAGE_EXE")
    if override:
        path = Path(override)
        if not path.is_file():
            sys.exit(f"OPENCPPCOVERAGE_EXE is set but does not exist: {path}")
        return path
    on_path = shutil.which("OpenCppCoverage")
    if on_path:
        return Path(on_path)
    for candidate in _TOOL_CANDIDATES:
        if candidate.is_file():
            return candidate
    sys.exit(
        "OpenCppCoverage not found. Install it (choco install opencppcoverage) "
        "or set OPENCPPCOVERAGE_EXE to OpenCppCoverage.exe."
    )


@dataclass
class FileCoverage:
    lines: dict[int, int] = field(default_factory=dict)

    @property
    def valid(self) -> int:
        return len(self.lines)

    @property
    def covered(self) -> int:
        return sum(self.lines.values())

    @property
    def percent(self) -> float:
        return 100.0 * self.covered / self.valid if self.valid else 0.0


def _windows_path(path: Path) -> str:
    return str(path).replace("/", "\\")


def run_tests(tool: Path, build_dir: Path, out_dir: Path) -> None:
    exe_dir = build_dir / "Release"
    for name, exe, extra_env in TESTS:
        exe_path = exe_dir / exe
        if not exe_path.is_file():
            sys.exit(f"missing test executable: {exe_path} (build the tests first)")
        env = os.environ.copy()
        env.update(extra_env)
        report = out_dir / f"{name}.xml"
        argv = [
            str(tool), "--quiet", "--optimized_build",
            "--sources", _windows_path(BASE / "src"),
            "--modules", _windows_path(exe_dir) + "\\*",
            "--export_type", f"cobertura:{_windows_path(report)}",
            "--", exe,
        ]
        print(f"  coverage: {name}")
        result = subprocess.run(argv, cwd=exe_dir, env=env, capture_output=True,
                                text=True, errors="replace", timeout=1800)
        if result.returncode != 0:
            sys.exit(
                f"{name} failed under OpenCppCoverage (exit {result.returncode}):\n"
                f"{result.stdout[-2000:]}\n{result.stderr[-2000:]}"
            )
        if not report.is_file():
            sys.exit(f"OpenCppCoverage produced no report for {name}")


def parse_reports(out_dir: Path) -> dict[str, FileCoverage]:
    """Merge every cobertura report; keep files under this repository's ``src/``.

    A line counts as covered when *any* owning test executes it (union of
    hits), never as a sum: the same AppCore lines appear in every report of a
    test that loads the DLL, so adding report denominators would dilute the
    rate of files that only one test touches.
    """
    merged: dict[str, FileCoverage] = {}
    for report in sorted(out_dir.glob("*.xml")):
        root = ET.parse(report).getroot()
        for cls in root.iter("class"):
            key = _repo_relative(cls.get("filename") or "")
            if key is None:
                continue
            entry = merged.setdefault(key, FileCoverage())
            for line in cls.iter("line"):
                number = int(line.get("number", "0"))
                if number not in entry.lines:
                    entry.lines[number] = 0
                if int(line.get("hits", "0")) > 0:
                    entry.lines[number] = 1
    return merged


def _repo_relative(filename: str) -> str | None:
    """Map any path shape the exporter emits to ``src/<tail>``.

    Observed shapes: ``F:\\repo\\src\\a.cpp``, ``PROJECTS\\...\\3D-Reconstruction
    \\src\\a.cpp`` (drive letter dropped) and relative ``..\\..\\src\\a.cpp``.
    Everything else — including ONNX Runtime's build-machine paths that also
    contain ``\\src\\`` — is rejected by matching the repository directory name
    (plus the ``..\\..\\src`` shape for sources compiled from the build tree).
    """
    lowered = filename.replace("/", "\\").lower()
    markers = (
        f"{BASE.name.lower()}\\src\\",
        "..\\..\\src\\",  # relative to build\\Release of a top-level target
        "..\\src\\",      # relative to build\\tests of a test target
    )
    for marker in markers:
        if marker in lowered:
            tail = lowered[lowered.rindex(marker) + len(marker):]
            if tail:
                return "src/" + tail.replace("\\", "/")
    return None


def print_table(merged: dict[str, FileCoverage]) -> None:
    print()
    print(f"{'file':<58} {'covered':>7} {'valid':>6} {'rate':>7}")
    print("-" * 82)
    for name in sorted(merged):
        entry = merged[name]
        print(f"{name:<58} {entry.covered:>7} {entry.valid:>6} "
              f"{entry.percent:>6.1f}%")


def enforce(merged: dict[str, FileCoverage]) -> int:
    failures: list[str] = []
    for path, floor in sorted(FLOORS.items()):
        entry = merged.get(path)
        if entry is None or entry.valid == 0:
            failures.append(f"{path}: no coverage data (file missing from the report)")
            continue
        if entry.percent + 1e-9 < floor:
            failures.append(
                f"{path}: {entry.percent:.1f}% < floor {floor:.1f}% "
                f"({entry.covered}/{entry.valid} lines)")
    print()
    if failures:
        print("Coverage gate FAILED:")
        for failure in failures:
            print(f"  {failure}")
        return 1
    print(f"Coverage gate OK: {len(FLOORS)} file(s) at or above their floors.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", default=str(BASE / "build"))
    parser.add_argument("--measure", action="store_true",
                        help="print measured rates without enforcing the floors")
    args = parser.parse_args()

    tool = find_tool()
    build_dir = Path(args.build_dir)
    out_dir = build_dir / "coverage"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"OpenCppCoverage: {tool}")
    run_tests(tool, build_dir, out_dir)
    merged = parse_reports(out_dir)
    if not merged:
        sys.exit("no source files were found in the coverage reports")
    print_table(merged)

    if args.measure:
        missing = sorted(set(FLOORS) - set(merged))
        if missing:
            print("\nFloors without data: " + ", ".join(missing))
        return 0
    return enforce(merged)


if __name__ == "__main__":
    sys.exit(main())
