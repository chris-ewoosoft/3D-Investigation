"""Run the repository's Python dependency vulnerability gate.

CI and the pre-push hook call this single entry point so they cannot silently
drift to different lock sets or vulnerability exceptions.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCKFILES = (
    "requirements.lock",
    "requirements-test.lock",
    "requirements-docker.lock",
    "requirements-eval.lock",
    "requirements-llama-cpu.lock",
)

# Neither advisory currently has a patched release:
# * DiskCache exploitation needs an attacker who can already write into the
#   cache directory. The shipped container is unprivileged and owns /app/data.
# * RAGAS is installed only in the offline evaluation environment; the gate
#   feeds it repository-owned local fixtures and exposes no remote request path,
#   so its SSRF-capable multimodal collection helper is unreachable.
# Do not broaden these exceptions; remove each one when upstream ships a fix.
IGNORED_VULNERABILITIES = ("PYSEC-2026-2447", "PYSEC-2026-3046")
LLAMA_URL_PATTERN = re.compile(
    r"^llama-cpp-python @ .*/download/v(?P<tag>\d+(?:\.\d+){2})/"
    r"llama_cpp_python-(?P<wheel>\d+(?:\.\d+){2})-",
    re.MULTILINE,
)
LLAMA_VERSION_ERROR = (
    "llama-cpp-python URL must pin matching release-tag and wheel versions"
)


def build_command(lockfile: str, *, require_hashes: bool = True) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "pip_audit",
        # Parse the fully pinned lock files directly. Besides avoiding an
        # unnecessary temporary install, this lets Windows developers audit
        # the intentionally Linux-only llama.cpp wheel before pushing.
        "--disable-pip",
        "--desc",
        "off",
    ]
    if require_hashes:
        command.append("--require-hashes")
    else:
        command.append("--no-deps")
    for vulnerability in IGNORED_VULNERABILITIES:
        command.extend(("--ignore-vuln", vulnerability))
    command.extend(("--requirement", lockfile))
    return command


def llama_version_projection() -> str:
    lock = (ROOT / "requirements-llama-cpu.lock").read_text(encoding="utf-8")
    match = LLAMA_URL_PATTERN.search(lock)
    if match is None or match["tag"] != match["wheel"]:
        raise ValueError(LLAMA_VERSION_ERROR)
    return f"llama-cpp-python=={match['wheel']}\n"


def main() -> int:
    missing = [name for name in LOCKFILES if not (ROOT / name).is_file()]
    if missing:
        print(f"Missing dependency lock(s): {', '.join(missing)}", file=sys.stderr)
        return 2

    print(
        "Auditing hash-pinned locks; accepted advisory: "
        + ", ".join(IGNORED_VULNERABILITIES),
        flush=True,
    )
    failed: list[str] = []
    for lockfile in LOCKFILES:
        print(f"\n==> {lockfile}", flush=True)
        if subprocess.run(
            build_command(lockfile), cwd=ROOT, check=False
        ).returncode:
            failed.append(lockfile)

    # pip-audit deliberately skips direct URL requirements because it cannot
    # infer a canonical distribution version from them. Project the version
    # only after proving the release tag and wheel filename agree, then audit
    # that canonical pin separately. Docker still verifies the real wheel hash.
    try:
        projection = llama_version_projection()
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        failed.append("llama-cpp-python URL projection")
    else:
        with tempfile.TemporaryDirectory(prefix="aiassistant-audit-") as directory:
            projected_lock = Path(directory) / "requirements-direct-url.txt"
            projected_lock.write_text(projection, encoding="utf-8")
            print(f"\n==> direct URL projection: {projection.strip()}", flush=True)
            if subprocess.run(
                build_command(str(projected_lock), require_hashes=False),
                cwd=ROOT,
                check=False,
            ).returncode:
                failed.append("llama-cpp-python URL projection")
    if failed:
        print(f"Dependency audit failed: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
