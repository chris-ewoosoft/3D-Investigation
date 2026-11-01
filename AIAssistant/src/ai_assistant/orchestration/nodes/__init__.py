"""Orchestration node sub-package.

Each module corresponds to a node in the LangGraph StateGraph:
  plan.py         — Plan node (generates step-by-step plan from planner LLM)
  plan_reflect.py — Plan Reflect node (critic validates the plan)
  reason.py       — Reason node (ReAct: select next tool or emit final answer)
  tool.py         — Tool node (execute the chosen tool, handle approval/UI-ack)
  reflect.py      — Reflect node (critic evaluates the tool result)
  summarize.py    — Observation summarizer (context compaction)
"""
from __future__ import annotations

from .plan import plan_node
from .plan_reflect import after_plan_reflect, plan_reflect_node
from .reason import ReasonContext, after_reason, reason_node
from .reflect import ReflectContext, after_reflect, reflect_node
from .summarize import summarize_messages
from .tool import ToolContext, after_tool, tool_node

__all__ = [
    "ReasonContext",
    "ReflectContext",
    "ToolContext",
    "after_plan_reflect",
    "after_reason",
    "after_reflect",
    "after_tool",
    "plan_node",
    "plan_reflect_node",
    "reason_node",
    "reflect_node",
    "summarize_messages",
    "tool_node",
]
