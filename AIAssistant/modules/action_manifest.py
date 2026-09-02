"""Shared, versioned desktop-action contract.

The JSON file is deliberately consumable by both the Python server and the Qt
client.  Do not duplicate actions, aliases or intent phrases in code.
"""
from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any

_MANIFEST_PATH = Path(__file__).resolve().parents[2] / "Config" / "agent_action_manifest.json"


def normalise_text(text: str) -> str:
    value = unicodedata.normalize("NFD", text.casefold())
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    value = value.replace("đ", "d")
    return re.sub(r"\s+", " ", value).strip()


@lru_cache(maxsize=1)
def manifest() -> dict[str, Any]:
    with _MANIFEST_PATH.open(encoding="utf-8") as source:
        data = json.load(source)
    if not isinstance(data.get("actions"), list):
        raise ValueError("agent_action_manifest.json requires an actions array")
    return data


@lru_cache(maxsize=1)
def _index() -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    actions: dict[str, dict[str, Any]] = {}
    aliases: dict[str, str] = {}
    for entry in manifest()["actions"]:
        action = entry["id"]
        actions[action] = entry
        for alias in entry.get("aliases", []):
            aliases[alias] = action
    return actions, aliases


def canonical_action(action: str) -> str | None:
    actions, aliases = _index()
    return action if action in actions else aliases.get(action)


def canonicalise_action_params(params: dict[str, Any]) -> dict[str, Any] | None:
    action = canonical_action(str(params.get("action", "")))
    if action is None:
        return None
    entry = _index()[0][action]
    allowed = {"action", "request_id", *entry.get("parameters", {}).keys()}
    result = {key: value for key, value in params.items() if key in allowed}
    result["action"] = action
    return result


def validate_action_params(params: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    result = canonicalise_action_params(params)
    if result is None:
        valid_actions = ", ".join(sorted(action_ids()))
        return None, f"Unsupported desktop action: '{params.get('action', '')}'. Supported actions are: {valid_actions}"
    entry = _index()[0][result["action"]]
    for name, definition in entry.get("parameters", {}).items():
        value = result.get(name)
        if definition.get("required") and (value is None or value == ""):
            return None, f"{result['action']} requires parameter '{name}'"
        if value is not None and definition.get("enum") and value not in definition["enum"]:
            return None, f"{result['action']}.{name} must be one of {definition['enum']}"
    return result, None


def action_plan_match_score(action: str, plan_step: str) -> float | None:
    """Score whether a canonical UI action describes a plan step.

    This is a data-driven consistency check over the existing action contract,
    not a per-request rule.  It lets Reflect reject a successful-but-unrelated
    desktop action when the LLM claims it completed the current plan step.
    ``None`` means the action or plan text could not be scored.
    """
    canonical = canonical_action(action)
    if not canonical or not isinstance(plan_step, str) or not plan_step.strip():
        return None
    
    # First, use the robust longest-substring match algorithm
    matched_actions = extract_actions_from_text(plan_step)
    if canonical in matched_actions:
        return 1.0
        
    # If there are valid exact substring matches and this action isn't one of them,
    # reject it to prevent substring overlap false positives (like 'mo hinh' vs 'an mo hinh').
    if matched_actions:
        return 0.0

    # Fallback for text that might be slightly misspelled or not exactly match a phrase
    entry = _index()[0].get(canonical)
    if not entry:
        return None
    step_tokens = set(re.findall(r"[a-z0-9]+", normalise_text(plan_step)))
    if not step_tokens:
        return None
    candidates = [canonical.replace(".", " ").replace("_", " ")]
    candidates.extend(str(phrase) for phrase in entry.get("phrases", []))
    scores = []
    for phrase in candidates:
        phrase_tokens = set(re.findall(r"[a-z0-9]+", normalise_text(phrase)))
        if phrase_tokens:
            scores.append(len(step_tokens & phrase_tokens) / len(phrase_tokens))
    return max(scores, default=0.0)


def action_matches_plan_step(action: str, plan_step: str, threshold: float = 0.75) -> bool | None:
    """Return a data-driven action/plan consistency decision."""
    score = action_plan_match_score(action, plan_step)
    return None if score is None else score >= threshold


def looks_like_ui_action(text: str) -> bool:
    value = normalise_text(text)
    return any(normalise_text(phrase) in value for entry in manifest()["actions"] for phrase in entry.get("phrases", []))


def match_action_intent(text: str) -> dict[str, Any] | None:
    value = normalise_text(text)
    # Language needs an explicit value; this belongs to the manifest action.
    if any(phrase in value for phrase in ("doi sang english", "change to english", "switch to english")):
        return {"action": "language.change", "language": "en"}
    if any(phrase in value for phrase in ("doi sang tieng viet", "change to vietnamese", "switch to vietnamese")):
        return {"action": "language.change", "language": "vi"}
    
    matched_actions = extract_actions_from_text(text)
    if matched_actions:
        action_id = matched_actions[0]
        if action_id == "language.change":
            return None
        result: dict[str, Any] = {"action": action_id}
        if action_id == "admin.login":
            result.update({"username": "Admin", "password": "1"})
        return result
    return None


def match_action_sequence(text: str) -> list[dict[str, Any]] | None:
    """Return a manifest-defined ordered UI workflow, if the text matches one."""
    value = normalise_text(text)
    for workflow in manifest().get("workflows", []):
        if not any(normalise_text(phrase) in value for phrase in workflow.get("phrases", [])):
            continue
        actions = workflow.get("actions", [])
        if all(canonical_action(action) is not None for action in actions):
            return [{"action": action} for action in actions]
    return None


def action_ids() -> set[str]:
    return set(_index()[0])

def extract_actions_from_text(text: str) -> list[str]:
    """Find the best non-overlapping manifest actions in the text using longest-match."""
    normalized = normalise_text(text)
    all_matches = []
    for entry in manifest()["actions"]:
        for phrase in entry.get("phrases", []):
            np = normalise_text(phrase)
            if not np:
                continue
            idx = normalized.find(np)
            if idx != -1:
                all_matches.append((len(np), idx, idx + len(np), entry["id"], phrase))

    # Sort matches by length (descending) to prioritize longer, more specific phrases
    all_matches.sort(key=lambda x: x[0], reverse=True)
    
    kept_hits = []
    covered_indices = set()
    for match in all_matches:
        _, start, end, action_id, phrase = match
        # If this match overlaps with any already accepted (longer) match, discard it
        if any(i in covered_indices for i in range(start, end)):
            continue
        
        kept_hits.append((start, action_id, phrase))
        covered_indices.update(range(start, end))
        
    kept_hits.sort()
    return [action_id for _, action_id, _ in kept_hits]


def split_step_by_manifest_phrases(step_text: str) -> list[str]:
    """Nếu một plan step khớp >1 canonical action phrase, tách thành nhiều step
    atomic theo thứ tự xuất hiện trong câu. Thuần data-driven từ manifest,
    không hard-code danh sách action — tự động áp dụng cho action mới thêm sau này.

    Trả về action id (canonical label) thay vì raw phrase để các bước con vẫn
    được semantic-match chính xác ở Reflect node.
    """
    normalized = normalise_text(step_text)
    all_matches = []
    for entry in manifest()["actions"]:
        for phrase in entry.get("phrases", []):
            np = normalise_text(phrase)
            if not np:
                continue
            idx = normalized.find(np)
            if idx != -1:
                all_matches.append((len(np), idx, idx + len(np), entry["id"], phrase))

    all_matches.sort(key=lambda x: x[0], reverse=True)
    
    kept_hits = []
    covered_indices = set()
    for match in all_matches:
        _, start, end, action_id, phrase = match
        if any(i in covered_indices for i in range(start, end)):
            continue
        kept_hits.append((start, action_id, phrase))
        covered_indices.update(range(start, end))
        
    if len(kept_hits) <= 1:
        return [step_text]
    
    kept_hits.sort()
    seen, ordered = set(), []
    for start, action_id, phrase in kept_hits:
        if action_id not in seen:
            seen.add(action_id)
            entry = _index()[0].get(action_id, {})
            phrases = entry.get("phrases", [phrase])
            label = phrases[0] if phrases else phrase
            ordered.append(label)
    return ordered

