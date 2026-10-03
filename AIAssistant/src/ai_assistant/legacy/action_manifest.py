# ruff: noqa: F401
"""
Compatibility shim for the legacy action_manifest.
New code should use ai_assistant.tools.action_manifest directly.
"""

from ai_assistant.tools.action_manifest import (
    action_catalog,
    action_ids,
    action_intents,
    canonical_action,
    canonicalise_action_params,
    manifest,
    normalize_text,
    reload_manifest,
    validate_action_params,
)


def looks_like_ui_action(text: str) -> bool:
    # Deprecated: A2A routing replaces text-based intent matching.
    return False

def rank_actions_for_step(step_text: str) -> list[tuple[str, int]]:
    # Re-implemented here briefly to avoid exposing it in the core since it's mostly unused
    from ai_assistant.tools.action_manifest import _intent_index
    haystack = f" {normalize_text(step_text)} "
    ranked = []
    for action_id, phrases in _intent_index().items():
        score = max((words for needle, words in phrases if needle in haystack), default=0)
        if score:
            ranked.append((action_id, score))
    ranked.sort(key=lambda item: (-item[1], item[0]))
    return ranked

def step_matches_action(step_text: str, action: str) -> bool | None:
    canonical = canonical_action(action)
    ranked = rank_actions_for_step(step_text)
    if canonical is None or not ranked:
        return None
    return dict(ranked).get(canonical, 0) >= ranked[0][1]
