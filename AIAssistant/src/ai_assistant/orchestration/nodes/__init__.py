"""Orchestration node sub-package.

Each module here corresponds to a node in the LangGraph StateGraph:
  plan.py       — Plan node (generates step-by-step plan from planner LLM)
  plan_reflect.py — Plan Reflect node (critic validates the plan)  
  reason.py     — Reason node (ReAct: select next tool or emit final answer)
  tool.py       — Tool node (execute the chosen tool, handle approval/UI-ack)
  reflect.py    — Reflect node (critic evaluates the tool result)
  summarize.py  — Observation summarizer (context compaction)

Migration status: These are currently stubs delegating to LangGraphAgent.py.
Each file contains the extracted node logic as it gets migrated in Phase 4.
"""
