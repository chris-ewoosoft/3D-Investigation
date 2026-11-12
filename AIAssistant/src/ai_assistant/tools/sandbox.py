"""Policy-first sandbox for destructive agent tools.

Docker isolation is opt-in because the Qt desktop distribution must still work
without Docker.  Even the local fallback uses an allow-list and blocks shell
metacharacters rather than executing arbitrary commands.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any

from ai_assistant.exec_policy import executable_candidates, resolve_executable
from ai_assistant.path_policy import canonical_path
from ai_assistant.settings import agent_write_roots, load_agent_runtime_settings

_ALLOWED = {"cmake", "ctest", "python", "pytest", "ruff", "git"}
# ADR 0011: allow-list must restrict subcommand / flag; deny arbitrary code execution.
# An entry ending in "/" is a path prefix (``pytest tests/…``); a bare word must
# match an entry verbatim. Flags (``-q``/``--build``) are accepted by position,
# except the two that turn an allow-listed binary into an escape hatch
# (``cmake -E …`` and ``git -c …``), which carry their own rules below.
_ALLOWED_SUBCOMMANDS = {
    "cmake": {"-E", "--build", "--install", "--version", "-S", "-B", "-P"},
    "git": {
        "status", "log", "diff", "add", "commit", "branch", "checkout", "clone",
        "pull", "push", "fetch", "merge", "rebase", "tag",
        # read-only / local-history operations added with the enforced list
        "show", "stash", "remote", "blame", "ls-files", "describe", "shortlog",
        "bisect", "init", "mv", "reset", "restore", "revert", "cherry-pick",
    },
    "pytest": {"tests/", "-k", "-m", "-x", "-q", "--tb"},
    "python": {"-m", "--version", "--help"},
    "ruff": {"check", "format", "--version", "-q"},
    "ctest": {"-R", "-N", "--output-on-failure", "--timeout"},
}
# Binaries that legitimately take a script / test-path / build-dir positional.
_PATH_POSITIONAL = frozenset({"python", "pytest", "ruff", "ctest", "cmake"})
# ``cmake -E`` sub-sub-commands that cannot launch a process or write outside
# the workspace. ``env`` and ``chdir`` are the documented escapes (they run the
# remainder of the command line), so everything not listed here is refused.
_CMAKE_E_BENIGN = frozenset({"cmake_echo_color", "cat", "md5sum", "sha1sum", "true", "false"})
# Deny-list for paths that should never be modified by agent (policy files, secrets,
# task state, VCS metadata). Exact basenames in _DENIED_WRITE_PATHS plus the
# substring list in write_file() cover both "pyproject.toml anywhere" and
# ".git/config" / "tasks.sqlite" style paths.
_DENIED_WRITE_PATHS = {
    "sandbox.py", "security.py", ".env", ".env.example", "pyproject.toml",
    "tasks.sqlite", "pending_agent_actions.json", "agent_audit.jsonl",
}
#: The same set, folded to lower case. Windows filesystems are
#: case-insensitive, so ``PYPROJECT.TOML`` *is* ``pyproject.toml``: a
#: case-sensitive comparison let the variant through, and the write replaced
#: the real file. The substring list cannot cover this on its own —
#: ``pyproject.toml`` is the one entry that is not a path fragment.
_DENIED_WRITE_NAMES = frozenset(name.lower() for name in _DENIED_WRITE_PATHS)

# ADR 0032: directories that hold the platform's *own policy*. The basename
# set above named two policy files and left the rest writable, so an approved
# write could rewrite the transport policy (``config/base.toml`` holds the
# origin allow-list), the A2A capability→scope map (``config/agents.toml``),
# the tool catalogue (``Config/agent_tools.json``) or the approval and
# execution policy of the assistant itself (``domain/approvals.py``,
# ``application/tools.py``).
#
# These are relative to the project root and matched on the resolved path, so
# a symlink cannot walk around them. They are *not* a substitute for the
# human approval the write already requires — they are the defence in depth
# that keeps a prompt-injected agent from editing the rules it is judged by.
#
# The list is **the package itself**, not a hand-maintained enumeration of the
# policy files: every earlier version of this list named two files, then the
# "policy trees", and each revision left a sibling writable — ``agents/``,
# ``adapters/persistence/``, ``config/``, ``settings.py``, ``path_policy.py``,
# ``exec_policy.py``. A rule that has to be extended by hand is a rule that
# goes stale; the rule is now "the platform's own source is not writable", and
# a new module cannot be added to the platform without being denied.
_DENIED_WRITE_TREES = (
    # Every layer of the Python platform: the domain rules, the application
    # chokepoint, the composition root, the adapters (transport policy, the
    # at-rest cipher, the tenant ownership) and the two kernel leaves
    # (``path_policy``/``exec_policy``) that every gate resolves through.
    "AIAssistant/src/ai_assistant",
    # The deployment configuration: the origin allow-list, the A2A
    # capability→scope map and the tool catalogue.
    "AIAssistant/config",
    # The repository's own configuration (the tool catalogue, the action
    # manifest, the model pins).
    "Config",
    # The C++ core the assistant drives.
    "src/core",
    # The C++ plugin-facing surface (the public interfaces the app and the
    # plugins are compiled against). It moved out of src/core, and a tree that
    # is not named here would be writable.
    "src/include",
)
# Preserve block for metacharacters but avoid false substring matches using token-level checks.
# "${" covers ${VAR}/$(( ))/${...} expansion forms that "$(" alone misses (ADR 0011).
_FORBIDDEN_METACHAR = {"&&", "||", ";", "|", ">", "<", "`", "$(", "${"}
# Block dangerous words only when they appear as standalone tokens (mitigate false positive on "del"/"rm")
_FORBIDDEN_STANDALONE = {"rm", "del", "format", "shutdown"}

# Interpreters whose command line can carry inline code. For these the only
# accepted trailing arguments are a script path or the benign version/help
# flags: ``-c``/``-m``/``-`` (and their attached forms, e.g. ``-cprint(1)``)
# turn the allow-list into arbitrary code execution.
_INTERPRETERS = frozenset({"python", "python.exe", "python3", "python3.exe"})
_SAFE_INTERPRETER_FLAGS = frozenset({"--version", "--help", "-V", "-h", "-q", "-B", "-s", "-E"})

# Flags that turn git into an arbitrary-execution or out-of-repository hatch
# wherever they appear on the command line. ADR 0011: naming the binary is
# not an allow-list if a flag can re-open it — ``-c core.sshCommand=<cmd>``
# runs a program on the next fetch, ``--upload-pack`` / ``--receive-pack``
# name the remote-side program git launches, ``--exec-path`` swaps the binary
# set git runs, and ``--git-dir`` / ``--work-tree`` retarget every operation
# at another repository.
_GIT_ESCAPE_FLAGS = frozenset({
    "-c", "-C", "--config", "--config-env", "--upload-pack", "--receive-pack",
    "--exec-path", "--git-dir", "--work-tree", "--namespace", "--attr-source",
})
# ``ext::<cmd>`` is git's remote helper that shells out to an arbitrary
# program; it only has to appear as any argument (clone URL, remote URL).
_GIT_REMOTE_HELPER_PREFIX = "ext::"
# ``git bisect run <cmd>`` executes an arbitrary verdict hook; every other
# bisect verb only records local state in the repository's own metadata.
_GIT_BISECT_VERBS = frozenset({
    "start", "good", "bad", "skip", "reset", "replay", "visualize", "log", "view",
})
# cmake flags whose value is a path governed by workspace confinement:
# ``-P`` runs a script and ``-S``/``-B``/``--build``/``--install`` operate on
# a tree whose build system executes commands of its own choosing.
_CMAKE_PATH_FLAGS = frozenset({"-P", "-S", "-B", "--build", "--install"})
# Benign **leading** options — the only flags allowed *before* the verb. A
# token starting with ``-`` is not "just a flag": ``git -C <dir> clean -fdx``
# retargets the verb, ``git -ccore.sshCommand=<cmd>`` injects config, and
# ``pytest -p <mod>`` imports code. So the pre-verb surface is an allow-list
# and the verb is the first positional token.
_ALLOWED_FLAGS: dict[str, frozenset[str]] = {
    "git": frozenset({"--no-pager", "-p", "--paginate", "--help", "-h"}),
    "cmake": frozenset({"-E", "--build", "--install", "--version", "-S", "-B", "-P", "--help", "-h"}),
    "pytest": frozenset({"-x", "-q", "-k", "-m", "--tb", "--help", "-h"}),
    "python": frozenset({"--version", "--help", "-V", "-h", "-B", "-s", "-E"}),
    "ruff": frozenset({"-q", "--version", "--help", "-h"}),
    "ctest": frozenset({"-R", "-N", "--output-on-failure", "--timeout", "--help", "-h"}),
}
# Allowed options that carry a value: the value is an argument, not the verb.
_VALUE_FLAGS: dict[str, frozenset[str]] = {
    "pytest": frozenset({"-k", "-m", "--tb"}),
    "ctest": frozenset({"-R", "--timeout"}),
}
# Options that read or write a path the sandbox cannot confine, refused
# wherever they appear on the line. The *value* is not the point — the
# **file** is: ``ruff --config <file>`` echoes the file's content when it
# cannot parse it, ``ruff check <dir>`` prints the offending source lines,
# ``pytest --junitxml=<file>`` writes outside the workspace, and
# ``git diff --no-index <a> <b>`` prints two arbitrary files.
_DANGEROUS_OPTIONS: dict[str, dict[str, str]] = {
    "pytest": {
        "-p": "a plugin to import",
        "--pyargs": "a module to import",
        "-c": "a configuration file",
        "-o": "inline configuration (it can inject a plugin)",
        "--override-ini": "inline configuration (it can inject a plugin)",
        "--rootdir": "the root directory",
        "--basetemp": "a temporary directory to write",
        "--import-mode": "the import mode",
        "--confcutdir": "a configuration search directory",
        "--junitxml": "a report file to write",
        "--log-file": "a log file to write",
        "--report-log": "a report file to write",
        "-n": "the worker count (it loads a plugin)",
        "--dist": "the distribution mode (it loads a plugin)",
        "--boxed": "a plugin",
        "--forked": "a plugin",
        "--cov": "a plugin",
        "--cov-report": "a plugin option",
        "--cov-config": "a configuration file",
    },
    "ruff": {
        "--config": "a configuration file (ruff echoes its content when it "
                   "cannot parse it; the project's own config is found "
                   "automatically)",
    },
    "git": {
        "--no-index": "two arbitrary files to diff",
        "--template": "a commit template to read",
        "--reference": "a repository to read objects from",
        "--pathspec-from-file": "a file of pathspecs to read",
    },
}
#: Binaries whose **positional** argument is a path the tool reads. The
#: value must stay inside the workspace: ``ruff check <dir>`` prints the
#: source lines of every file it finds, so a directory outside the workspace
#: is a read primitive.
_PATH_POSITIONAL_READERS = frozenset({"python", "pytest", "ruff"})
# git verbs whose positional destination is a filesystem path.
_GIT_PATH_VERBS = frozenset({"clone", "init"})



def _inline_code_requested(argv: list[str]) -> bool:
    """Return True when an interpreter argument would execute inline code."""
    if Path(argv[0]).name.casefold() not in _INTERPRETERS:
        return False
    for argument in argv[1:]:
        if argument in {"-", "--"}:
            return True  # stdin marker / option terminator are never legitimate here
        if not argument.startswith("-"):
            continue  # script path
        if argument in _SAFE_INTERPRETER_FLAGS:
            continue
        if argument.startswith("--"):
            if argument.split("=", 1)[0] in _SAFE_INTERPRETER_FLAGS:
                continue
            return True
        return True  # short option, possibly with an attached value (-c, -cCODE, -m, -mmod)
    return False


def _policy_rejection(command: str) -> str | None:
    """Reject shell metacharacters and dangerous standalone tokens (ADR 0011).

    Token-level matching keeps ``confirm performance`` usable while ``rm`` as a
    word of its own is refused.
    """
    for meta in _FORBIDDEN_METACHAR:
        if meta in command:
            return "Command rejected by sandbox policy (metacharacter)."
    for token in command.casefold().split():
        if token.lstrip() in _FORBIDDEN_STANDALONE:
            return "Command rejected by sandbox policy (dangerous command token)."
    return None


def _looks_like_path(argument: str) -> bool:
    """True for a script/test/build-dir positional rather than a subcommand."""
    return "/" in argument or "\\" in argument or argument.startswith(".") or argument.endswith(".py")


def _executable_rejection(executable: str) -> str | None:
    """Refuse anything that is not a bare allow-listed name resolved via PATH.

    Matching on ``Path(executable).name`` alone let ``/tmp/attacker/git`` pass
    for ``git``: the allow-list has to constrain *which* program runs, not what
    a file happens to be called. A name carrying no separator is resolved by the
    OS against the ``PATH`` this process inherits, so only the operator's own
    tool can match — in the local runtime and in the container alike.
    """
    if "/" in executable or "\\" in executable:
        return "Executable must be a bare name from the allow-list, resolved via PATH."
    if executable.casefold() not in _ALLOWED:
        return "Executable is not in the agent allow-list."
    return None


def _canonical_path(path: Path) -> Path:
    """One canonical spelling of ``path``, with symlinks followed.

    The implementation moved to :mod:`ai_assistant.path_policy` — it is
    kernel policy, and the LSP read gate and the HTTP attachment gate need
    the same spelling rule. This alias keeps the historical name working.
    """
    return canonical_path(path)


def _path_escapes_workspace(value: str, cwd: str) -> bool:
    """True when ``value`` resolves outside the directory the command runs in.

    Relative targets are resolved against the run's ``cwd`` — the directory
    the subprocess actually gets — and the resolution follows symlinks /
    junctions and expands ``..`` before the comparison, so a link planted
    inside the workspace can not smuggle an outside target past the gate.
    Both sides are spelled the same way (``_canonical_path``), otherwise a
    path that never left the workspace reads as an escape.
    """
    target = Path(value)
    if not target.is_absolute():
        target = Path(cwd) / target
    try:
        _canonical_path(target).relative_to(_canonical_path(Path(cwd)))
    except ValueError:
        return True
    return False


def _git_flag_rejection(arguments: list[str]) -> str | None:
    """Refuse git flags and remote helpers that re-open the allow-list.

    The comparison is on the option **head**, so the attached short forms
    (``-ccore.sshCommand=…``) are caught too: splitting on ``=`` alone left
    ``-ccore.sshCommand`` untouched, and git accepts the attached spelling.
    """
    for argument in arguments:
        head = argument.split("=", 1)[0]
        if head in _GIT_ESCAPE_FLAGS or argument == "-c" or argument.startswith("-c"):
            if head == "-c":
                return "Sandbox: git -c config injection blocked."
            return f"Sandbox: git flag '{head}' is blocked (command/config escape)."
        if argument.startswith(_GIT_REMOTE_HELPER_PREFIX):
            return "Sandbox: git remote helper 'ext::' (arbitrary command) is blocked."
    return None


def _looks_like_url(argument: str) -> bool:
    """True for a git remote URL (``https://…``, ``git@…``), not a local path."""
    return "://" in argument or argument.startswith("git@")


def _git_path_rejection(arguments: list[str], cwd: str) -> str | None:
    """Confine ``git clone``/``git init`` destinations to the workspace.

    ``git clone <url> <dest>`` and ``git init <dest>`` create a directory at
    the destination. Confining only the cmake/python positionals let a clone
    plant a repository (or a hook) outside the directory the command runs in.
    The remote URL is skipped; every other path-like positional is checked.
    """
    verb = next((argument for argument in arguments if not argument.startswith("-")), None)
    if verb not in _GIT_PATH_VERBS:
        return None
    for argument in arguments:
        if argument.startswith("-") or _looks_like_url(argument):
            continue
        if _looks_like_path(argument) and _path_escapes_workspace(argument, cwd):
            return f"Sandbox: git {verb} destination must stay inside the workspace."
    return None


def _bisect_rejection(remainder: list[str]) -> str | None:
    """``git bisect run <cmd>`` executes an arbitrary verdict hook."""
    verb = remainder[0] if remainder else None
    if verb is not None and verb not in _GIT_BISECT_VERBS:
        return "Sandbox: 'git bisect run' (arbitrary verdict hook) is blocked."
    return None


def _cmake_path_rejection(arguments: list[str], cwd: str) -> str | None:
    """Confine cmake's script / build-tree flags to the workspace (ADR 0011)."""
    for index, argument in enumerate(arguments):
        flag = argument.split("=", 1)[0]
        if flag not in _CMAKE_PATH_FLAGS:
            continue
        value = (argument.split("=", 1)[1] if "=" in argument
                 else arguments[index + 1] if index + 1 < len(arguments) else "")
        if not value or _path_escapes_workspace(value, cwd):
            return f"Sandbox: cmake {flag} must target a path inside the workspace."
    return None


def _path_positional_rejection(executable: str, arguments: list[str], cwd: str) -> str | None:
    """Confine the **path positionals** of the allow-listed binaries.

    ``python /tmp/evil.py`` and ``pytest /tmp/...`` used to pass the
    allow-list on the ``_looks_like_path`` shape alone: the gate checked that
    the argument *looked* like a path, never that it stayed inside the
    directory the subprocess runs in. A script outside ``cwd`` is arbitrary
    code execution with the workspace mounted.

    ``ruff`` is in the same class: ``ruff check <dir>`` prints the
    **source lines** of every file it finds, so a directory outside the
    workspace is a read primitive (ADR 0035 closed the same hole for the LSP
    read gate; the corpus builder had the same one — ADR 0038).
    """
    if executable not in _PATH_POSITIONAL_READERS:
        return None
    for argument in arguments:
        if argument.startswith("-"):
            continue
        if not _looks_like_path(argument):
            continue
        if _path_escapes_workspace(argument, cwd):
            return f"Sandbox: {executable} path must stay inside the workspace."
    return None


def _cmake_option_rejection(rest: list[str]) -> str | None:
    """cmake's first token is a verb-like option; reject anything else."""
    first = rest[0]
    if first == "-E":
        sub_sub = rest[1] if len(rest) > 1 else None
        if sub_sub in _CMAKE_E_BENIGN:
            return None
        return "Sandbox: dangerous cmake -E subcommand blocked."
    head = first.split("=", 1)[0]
    if first not in _ALLOWED_FLAGS["cmake"] and head not in _ALLOWED_FLAGS["cmake"]:
        return f"Sandbox: cmake option '{head}' is not in the allow-list."
    return None


def _ctest_option_rejection(rest: list[str]) -> str | None:
    """ctest has no verb: every token is an option or an option value."""
    index = 0
    while index < len(rest):
        token = rest[index]
        head = token.split("=", 1)[0]
        if token not in _ALLOWED_FLAGS["ctest"] and head not in _ALLOWED_FLAGS["ctest"]:
            return f"Sandbox: ctest option '{head}' is not in the allow-list."
        index += 1
        if token in _VALUE_FLAGS["ctest"] and "=" not in token:
            index += 1  # the option's value is not an option
    return None


def _dangerous_option_rejection(executable: str, rest: list[str]) -> str | None:
    """Refuse the options that read or write a path the sandbox cannot confine.

    The check runs over **every** token of the line, before and after the verb:
    ``ruff check --config <file>`` and ``pytest tests/ -p <mod>`` both put the
    dangerous option *after* the verb, and a pre-verb-only check would miss
    them.
    """
    dangerous = _DANGEROUS_OPTIONS.get(executable, {})
    for token in rest:
        head = token.split("=", 1)[0]
        if head in dangerous:
            return (
                f"Sandbox: {executable} option '{head}' is not in the allow-list "
                f"({dangerous[head]})."
            )
    return None


def _verb_rejection(executable: str, rest: list[str]) -> str | None:
    """The verb is the first positional; only allowed flags precede it."""
    allowed: set[str] = _ALLOWED_SUBCOMMANDS.get(executable, set())
    flags = _ALLOWED_FLAGS.get(executable, frozenset())
    value_flags = _VALUE_FLAGS.get(executable, frozenset())
    saw_verb = False
    index = 0
    while index < len(rest):
        token = rest[index]
        if saw_verb:
            # A positional or a plain flag after the verb is an argument to it.
            # The flags that can execute/import are refused by the per-binary
            # checks that run before this scan (``_git_flag_rejection``,
            # ``_pytest_flag_rejection``, ``_cmake_path_rejection``).
            index += 1
            continue
        if token.startswith("-"):
            head = token.split("=", 1)[0]
            if token in flags or head in flags:
                index += 1
                if token in value_flags and "=" not in token:
                    index += 1  # the option's value is not the verb
                continue
            return f"Sandbox: flag '{head}' is not in the {executable} allow-list."
        if token in allowed:
            saw_verb = True
            index += 1
            continue
        if any(entry.endswith("/") and token.startswith(entry) for entry in allowed):
            saw_verb = True
            index += 1
            continue
        if executable in _PATH_POSITIONAL and _looks_like_path(token):
            saw_verb = True
            index += 1
            continue
        return f"Sandbox: subcommand '{token}' is not in the {executable} allow-list."
    return None


def _subcommand_rejection(argv: list[str], cwd: str) -> str | None:
    """Restrict the subcommand surface of allow-listed binaries (ADR 0011).

    Naming the executable alone is not an allow-list: ``cmake -E env <cmd>``
    runs an arbitrary process, ``git -c core.sshCommand=<cmd>`` is RCE from
    config on the next fetch, and ``git -C <dir> clean -fdx`` retargets the
    verb — a leading token that starts with ``-`` is not "just a flag". The
    verb is therefore the first positional, only the allowed leading options
    may precede it, and the flags that can execute or import are refused
    anywhere on the line.
    """
    executable = Path(argv[0]).name.casefold()
    rest = argv[1:]
    if not rest:
        return None
    # The dangerous options are refused for **every** binary, before the
    # per-binary branches: they are the options that read or write a path the
    # sandbox cannot confine, and they hide behind the verb
    # (``ruff check --config <file>``).
    dangerous_rejection = _dangerous_option_rejection(executable, rest)
    if dangerous_rejection:
        return dangerous_rejection
    if executable == "git":
        git_rejection = _git_flag_rejection(rest)
        if git_rejection:
            return git_rejection
        path_rejection = _git_path_rejection(rest, cwd)
        if path_rejection:
            return path_rejection
        if rest[0] == "bisect":
            bisect_rejection = _bisect_rejection(rest[1:])
            if bisect_rejection:
                return bisect_rejection
    elif executable == "cmake":
        cmake_rejection = _cmake_path_rejection(rest, cwd)
        if cmake_rejection:
            return cmake_rejection
        return _cmake_option_rejection(rest)
    elif executable == "ctest":
        return _ctest_option_rejection(rest)
    positional_rejection = _path_positional_rejection(executable, rest, cwd)
    if positional_rejection:
        return positional_rejection
    return _verb_rejection(executable, rest)


def _docker_argv(argv: list[str], cwd: str, image: str) -> tuple[list[str], str]:
    """Wrap the command in a bounded, network-less container.

    The generated ``--name`` lets the timeout path force-remove the container:
    killing the ``docker run`` client does NOT stop the container it started.
    The hardening flags are part of the isolation contract (ADR 0023): no
    network (egress deny by policy), no Linux capabilities and no path to
    regaining them via setuid binaries.
    """
    container_name = f"agent-sbx-{uuid.uuid4().hex[:12]}"
    wrapped = ["docker", "run", "--rm", "--name", container_name,
               "--network", "none", "--cap-drop", "ALL",
               "--security-opt", "no-new-privileges",
               "--memory", "1g", "--cpus", "1.0",
               "--pids-limit", "128", "-v", f"{cwd}:/workspace:rw",
               "-w", "/workspace", image, *argv]
    return wrapped, container_name


def _executable_candidates(candidate: str) -> list[str]:
    """The spellings a bare program name may have on this platform.

    The implementation moved to :mod:`ai_assistant.exec_policy` — the
    resolution of a program is *one* rule, and the LSP client needs the same
    one. This alias keeps the historical name working.
    """
    return executable_candidates(candidate)


def _resolve_executable(name: str, path: str) -> str | None:
    """Resolve an allow-listed bare name to exactly one absolute PATH hit.

    The implementation moved to :mod:`ai_assistant.exec_policy` — the
    resolution of a program is *one* rule, and the LSP client needs the same
    one (``shutil.which`` there prepended the current directory to the
    search path, so a planted binary won over the operator's own tool).
    This alias keeps the historical name working.
    """
    return resolve_executable(name, path)


def run(command: str, cwd: str, timeout: int) -> dict[str, Any]:
    runtime_settings = load_agent_runtime_settings()
    if not runtime_settings.sandbox_enabled:
        return {"error": "Agent command sandbox is disabled by policy."}
    if runtime_settings.sandbox_require_isolation and runtime_settings.sandbox_runtime != "docker":
        # Production gate (ADR 0023): an allow-list is not an OS boundary, so
        # deployments that declare untrusted tool execution refuse to run
        # commands with the server's own privileges.
        return {"error": "Sandbox isolation is required by policy; set AGENT_SANDBOX_RUNTIME=docker."}
    rejection = _policy_rejection(command)
    if rejection:
        return {"error": rejection}
    try:
        argv = shlex.split(command, posix=os.name != "nt")
    except ValueError as error:
        return {"error": f"Invalid command: {error}"}
    if not argv:
        return {"error": "Executable is not in the agent allow-list."}
    executable_rejection = _executable_rejection(argv[0])
    if executable_rejection:
        return {"error": executable_rejection}
    # Inline code is refused before the subcommand scan: ``python -c …`` /
    # ``-m`` / ``-`` are interpreter escapes and the message is the one the
    # policy promises, not a generic "flag not in allow-list".
    if _inline_code_requested(argv):
        return {"error": "Inline interpreter code (-c, -m, -) is rejected by sandbox policy."}
    sub_rejection = _subcommand_rejection(argv, cwd)
    if sub_rejection:
        return {"error": sub_rejection}
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONUTF8": "1"}
    container_name = None
    sandbox_label = "local-allowlist"
    if runtime_settings.sandbox_runtime == "docker":
        argv, container_name = _docker_argv(argv, cwd, runtime_settings.sandbox_image)
        sandbox_label = "docker"
    else:
        # The container resolves the bare name against its own PATH (the host
        # cwd is not searched there); the local runtime must resolve it here,
        # or the OS searches the workspace before PATH on Windows.
        resolved = _resolve_executable(argv[0], env["PATH"])
        if resolved is None:
            return {"error": f"Executable '{argv[0]}' was not found on PATH."}
        argv = [resolved, *argv[1:]]
    try:
        proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                              timeout=min(timeout, 120), env=env, shell=False,
                              encoding="utf-8", errors="replace", check=False)
        return {"command": argv, "return_code": proc.returncode,
                "stdout": proc.stdout[:5000], "stderr": proc.stderr[:2000],
                "sandbox": sandbox_label}
    except subprocess.TimeoutExpired:
        if container_name:
            _force_remove_container(container_name)
        return {"error": f"Command timed out after {timeout}s"}


def _force_remove_container(container_name: str) -> None:
    """Best-effort ``docker rm -f`` so a timed-out sandbox cannot outlive the call.

    ADR 0011: the local runtime already has a hard ``timeout``; the Docker
    runtime must be equally bounded, otherwise ``docker run`` timing out leaves
    the container running with the workspace mounted read-write.
    """
    try:
        subprocess.run(["docker", "rm", "-f", container_name],
                       capture_output=True, timeout=15, shell=False, check=False)
    except (OSError, subprocess.SubprocessError):
        # The container may already be gone, or the daemon unreachable — the
        # command result is still the timeout error either way.
        pass


def _denied_tree_hit(relative: Path) -> bool:
    """Whether the project-relative path lies inside a denied policy tree.

    The path must already be *resolved*: ``write_file`` computes it from
    ``Path(path).resolve()``, so a symlink that points into a policy tree is
    caught too, and a relative path that only *looks* like it leaves the tree
    (via ``..``) is not.
    """
    parts = relative.parts
    for denied in _DENIED_WRITE_TREES:
        denied_parts = Path(denied).parts
        if parts[:len(denied_parts)] == denied_parts:
            return True
    return False


def write_file(path: str, content: str, project_root: str) -> dict[str, Any]:
    """Atomically write only allow-listed project subtrees and bounded payloads."""
    runtime_settings = load_agent_runtime_settings()
    if not runtime_settings.sandbox_enabled:
        return {"error": "Agent write sandbox is disabled by policy."}
    if len(content.encode("utf-8")) > runtime_settings.max_write_bytes:
        return {"error": "Write exceeds AGENT_MAX_WRITE_BYTES."}
    # The gate compares the *canonical* spelling of the target and of the
    # project root: the target is resolved (a symlink is followed, so a link
    # into a policy tree is caught) and both sides are spelled the same way,
    # so a path that never left the project root cannot read as an escape
    # just because Windows handed us two spellings of one directory.
    resolved = _canonical_path(Path(path))
    try:
        relative = resolved.relative_to(_canonical_path(Path(project_root)))
    except ValueError:
        return {"error": "Write target escapes the project root."}
    # ADR 0015 / ADR 0011 / ADR 0032: deny writing to policy / sandbox /
    # secrets paths. The deny-list is checked *before* the allow-list: it is
    # the stricter policy, and a policy file must be refused whether or not
    # its subtree happens to be writable.
    # The basename set covers "pyproject.toml anywhere"; the substring list
    # covers ".git/config", "tasks.sqlite", durable pending/audit state and
    # any case variant of the policy files. Both are matched on the resolved
    # target, so the name of the file a link points at is the one that counts.
    #
    # The basename set is compared **case-insensitively**: Windows filesystems
    # are case-insensitive, so ``PYPROJECT.TOML`` *is* ``pyproject.toml`` —
    # a case-sensitive comparison let the variant through and the write
    # replaced the real file. The substring list cannot cover this on its
    # own: ``pyproject.toml`` is the one entry that is not a path fragment.
    target_name = resolved.name
    resolved_lower = str(resolved).lower()
    if target_name.lower() in _DENIED_WRITE_NAMES or any(
        p in resolved_lower
        for p in ["sandbox.py", ".env", "security.py", ".git", "tasks.sqlite",
                  "pending_agent_actions.json", "agent_audit.jsonl"]
    ):
        return {"error": "Write target is denied by sandbox policy (policy file)."}
    if _denied_tree_hit(relative):
        return {"error": "Write target is denied by sandbox policy (policy tree)."}
    if not relative.parts or relative.parts[0] not in agent_write_roots():
        return {"error": "Write target is not in AGENT_WRITE_ALLOWLIST."}
    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", dir=target.parent,
                                         delete=False) as temporary:
            temporary.write(content)
            temporary_path = temporary.name
        os.replace(temporary_path, target)
        return {"success": True, "path": str(relative).replace("\\", "/"),
                "bytes_written": len(content.encode("utf-8")), "sandbox": "write-allowlist"}
    except OSError as error:
        return {"error": f"Unable to write sandboxed target: {error}"}


def create_directory(path: str, project_root: str) -> dict[str, Any]:
    """Create an allow-listed directory after explicit approval.

    The policy-tree deny is applied here too: creating a directory *inside*
    the platform's own source is the first step of planting a module there,
    and the write gate alone would only stop the second step.
    """
    if not load_agent_runtime_settings().sandbox_enabled:
        return {"error": "Agent directory sandbox is disabled by policy."}
    try:
        root = _canonical_path(Path(project_root))
        target = _canonical_path(Path(path))
        relative = target.relative_to(root)
    except ValueError:
        return {"error": "Directory target escapes the project root."}
    if _denied_tree_hit(relative):
        return {"error": "Directory target is denied by sandbox policy (policy tree)."}
    if not relative.parts or relative.parts[0] not in agent_write_roots():
        return {"error": "Directory target is not in AGENT_WRITE_ALLOWLIST."}
    try:
        existed = target.is_dir()
        target.mkdir(parents=True, exist_ok=True)
        return {"success": True, "path": str(relative).replace("\\", "/"), "created": not existed,
                "sandbox": "directory-allowlist"}
    except OSError as error:
        return {"error": f"Unable to create sandboxed directory: {error}"}
