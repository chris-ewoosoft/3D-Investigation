"""Specialist role vocabulary shared by orchestration, adapters and evals.

``Specialist`` used to live in ``orchestration.supervisor``, which forced the
A2A adapter to import an orchestration module (and the supervisor to import the
adapter back) to enumerate the advertised skills — an import cycle that only
worked because both sides hid the import in a ``try/except`` or a function
body.  Moving the enum into the domain makes it a framework-free contract every
layer may depend on inward (ADR 0001/0002).
"""
from __future__ import annotations

from enum import StrEnum


class Specialist(StrEnum):
    """One worker role the supervisor can hand a task to."""

    SUPERVISOR = "supervisor"
    CHATBOT = "chatbot"
    TOOLAPP = "toolapp"
    RESEARCH = "research"
    WORKFLOW = "desktop_workflow"
    CODE = "code"
    VERIFICATION = "verification"
    #: Tools contributed by an enabled third-party plugin. The role exists so
    #: plugin tools are agent-visible without granting them to a builtin
    #: specialist; the composition root fills its tool row from the live
    #: plugin catalogue, and the static row stays empty (fail-closed).
    PLUGIN = "plugin"


#: The supervisor's explicit handoff vocabulary: transfer tool name → the
#: specialist it routes to. This table is the naming authority for everything
#: handoff-shaped — supervisor routing, the least-privilege tool rows, and the
#: builtin marker handlers derive their names from it — so adding or removing
#: a handoff target is one edit here instead of five parallel ones
#: (architecture finding 12).
TRANSFER_TARGETS: dict[str, Specialist] = {
    "transfer_to_code_agent": Specialist.CODE,
    "transfer_to_toolapp_agent": Specialist.TOOLAPP,
    "transfer_to_chatbot_agent": Specialist.CHATBOT,
}

#: Tool classes, owned by the domain so the routing table, the policy contract
#: and the execution catalogue all read the same vocabulary.  A tool belongs
#: to a class once; ``TOOL_SPECIALISTS`` is the reverse index the supervisor
#: consumes.  LSP queries are read-only project inspection, so they belong to
#: the research class instead of being advertised-and-refused.
LSP_TOOLS = frozenset({"go_to_definition", "find_references"})
RESEARCH_TOOLS = frozenset({
    "read_file", "list_directory", "search_text", "analyze_code", "find_files",
    "git_diff", "rag_search", *LSP_TOOLS,
})
VERIFICATION_TOOLS = frozenset({"validate_file", "get_project_status"})
CODE_TOOLS = frozenset({
    "write_file", "patch_file", "replace_file_content",
    "multi_replace_file_content", "create_directory", "run_command",
})
WORKFLOW_TOOLS = frozenset({"application_action"})

#: Tools that declare no side effect of their own.  The policy contract may
#: use the read-only default only for a name in this set; any other unknown
#: name is a policy gap and fails at bootstrap instead of silently receiving
#: the weakest contract.
READ_ONLY_TOOLS = frozenset(RESEARCH_TOOLS | VERIFICATION_TOOLS | set(TRANSFER_TARGETS))


def _reverse_tool_index() -> dict[str, frozenset[Specialist]]:
    """Build tool → allowed specialists once, from the tool classes.

    ``CODE`` may run research/verification tools because a coding task
    inspects the repository before mutating it, and it may run every handoff
    marker; every other specialist gets exactly its own class.
    """
    rows: dict[str, set[Specialist]] = {}
    for name in RESEARCH_TOOLS:
        rows.setdefault(name, set()).update({Specialist.RESEARCH, Specialist.CODE})
    for name in VERIFICATION_TOOLS:
        rows.setdefault(name, set()).update({Specialist.VERIFICATION, Specialist.CODE})
    for name in CODE_TOOLS:
        rows.setdefault(name, set()).add(Specialist.CODE)
    for name in WORKFLOW_TOOLS:
        rows.setdefault(name, set()).add(Specialist.WORKFLOW)
    for name, specialist in TRANSFER_TARGETS.items():
        rows.setdefault(name, set()).update({Specialist.CODE, specialist})
    return {name: frozenset(specialists) for name, specialists in rows.items()}


#: The one tool → specialist authorization table.  ``supervisor.ALLOWED_TOOLS``
#: is its reverse index, and the tool policy contract reads it too, so a new
#: tool class cannot appear in routing without an authorization row (or the
#: other way round).
TOOL_SPECIALISTS: dict[str, frozenset[Specialist]] = _reverse_tool_index()

__all__ = [
    "CODE_TOOLS",
    "LSP_TOOLS",
    "READ_ONLY_TOOLS",
    "RESEARCH_TOOLS",
    "TOOL_SPECIALISTS",
    "TRANSFER_TARGETS",
    "VERIFICATION_TOOLS",
    "WORKFLOW_TOOLS",
    "Specialist",
]
