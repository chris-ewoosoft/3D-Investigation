"""LangGraph orchestration for the local 3D-Reconstruction agent.

The graph owns the agent loop; the host application owns model inference and
tool execution.  This keeps Qt-specific actions outside the Python process.

Architecture (ReAct + Plan-and-Execute):
  START -> plan -> reason -> tool (loop) -> END

  plan   : Sinh ke hoach (danh sach cac buoc) truoc khi bat dau tool loop.
  reason : Quan sat ket qua tool truoc do, quyet dinh buoc tiep theo hoac ket thuc.
  tool   : Thuc thi tool duoc chon.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

# Completion-signal tools: ngay sau khi success -> done=True, khong lap lai
_COMPLETION_TOOLS = {"application_action"}
_OBSERVATION_SUMMARY_THRESHOLD = 4   # tong ket observations sau N tool calls
_OBSERVATION_KEEP_LAST = 2           # giu lai N tool-result messages gan nhat


class AgentState(TypedDict):
    messages:        list[dict[str, str]]   # lich su hoi thoai
    steps:           list[dict[str, Any]]   # danh sach steps (cho UI)
    iteration:       int                    # so vong lap da chay
    temperature:     float                  # nhiet do sinh van ban
    done:            bool                   # da hoan thanh chua
    pending_tool:    dict[str, Any] | None  # tool dang cho phe duyet
    plan:            list[str] | None       # ke hoach cac buoc
    tool_call_count: int                    # so lan goi tool (trigger summarization)


Completion    = Callable[[list[dict[str, str]], float], str]
Parser        = Callable[[str], tuple[str | None, dict[str, Any] | None]]
Executor      = Callable[[str, dict[str, Any]], dict[str, Any]]
NeedsApproval = Callable[[str], bool]


class LocalAgentGraph:
    """ReAct + Plan-and-Execute graph cho local llama.cpp model."""

    def __init__(self, complete: Completion, parse: Parser, execute: Executor,
                 needs_approval: NeedsApproval, max_iterations: int) -> None:
        self._complete       = complete
        self._parse          = parse
        self._execute        = execute
        self._needs_approval = needs_approval
        self._max_iterations = max_iterations

        builder = StateGraph(AgentState)
        builder.add_node("plan",   self._plan)
        builder.add_node("reason", self._reason)
        builder.add_node("tool",   self._tool)

        builder.add_edge(START, "plan")
        builder.add_conditional_edges("plan", self._after_plan,
                                      {"reason": "reason", "end": END})
        builder.add_conditional_edges("reason", self._after_reason,
                                      {"tool": "tool", "end": END})
        builder.add_edge("tool", "reason")

        self._graph = builder.compile(checkpointer=MemorySaver())

    # ── Public API ─────────────────────────────────────────────────────────────

    def run(self, messages: list[dict[str, str]], session_id: str,
            temperature: float, steps: list[dict[str, Any]] | None = None,
            iteration: int = 0) -> AgentState:
        return self._graph.invoke(
            {
                "messages":        messages,
                "steps":           steps or [],
                "iteration":       iteration,
                "temperature":     temperature,
                "done":            False,
                "pending_tool":    None,
                "plan":            None,
                "tool_call_count": 0,
            },
            config={"configurable": {"thread_id": session_id}},
        )

    # ── Node: plan ─────────────────────────────────────────────────────────────

    def _plan(self, state: AgentState) -> dict[str, Any]:
        """Sinh ke hoach cac buoc. Skip neu cau ngan (likely UI action)."""
        messages = state["messages"]
        user_msg = messages[-1].get("content", "") if messages else ""

        # Skip planning cho cac cau ngan hoac UI actions
        if len(user_msg) < 80:
            print("[LOG: PLAN NODE] Cau ngan, skip planning.")
            return {"plan": None}

        planning_msgs = [
            *messages[:1],  # system prompt
            {
                "role": "user",
                "content": (
                    f"Truoc khi thuc thi, hay len ke hoach ngan gon cac buoc can lam cho yeu cau sau. "
                    f"Tra loi CHINH XAC theo dinh dang JSON array (khong kem text khac):\n"
                    f'["buoc 1", "buoc 2", ...]\n\n'
                    f"Yeu cau: {user_msg}"
                ),
            },
        ]
        print("[LOG: PLAN NODE] Dang sinh ke hoach...")
        raw = self._complete(planning_msgs, max(0.1, state["temperature"] - 0.1)).strip()
        print(f"[LOG: PLAN NODE] Ke hoach tho: {raw[:200]}")

        plan: list[str] | None = None
        try:
            start = raw.index("[")
            end   = raw.rindex("]") + 1
            plan  = json.loads(raw[start:end])
            if not isinstance(plan, list):
                plan = None
        except (ValueError, json.JSONDecodeError):
            plan = None

        if plan:
            steps = list(state["steps"])
            steps.append({"type": "plan", "steps": plan})
            print(f"[LOG: PLAN NODE] Ke hoach: {plan}")
            return {"plan": plan, "steps": steps}

        print("[LOG: PLAN NODE] Khong parse duoc ke hoach, tiep tuc khong co plan.")
        return {"plan": None}

    @staticmethod
    def _after_plan(state: AgentState) -> str:
        if state.get("done"):
            return "end"
        return "reason"

    # ── Node: reason ───────────────────────────────────────────────────────────

    def _reason(self, state: AgentState) -> dict[str, Any]:
        iteration = state["iteration"] + 1
        steps     = list(state["steps"])
        print(f"\n--- [LOG: REASON NODE] Vong lap thu {iteration} ---")

        if iteration > self._max_iterations:
            print(f"[LOG: REASON NODE] Dat gioi han ({self._max_iterations} iterations).")
            steps.append({"type": "final_answer",
                          "content": "Agent da dat gioi han so vong lap."})
            return {"iteration": iteration, "steps": steps, "done": True}

        messages        = list(state["messages"])
        plan            = state.get("plan")
        tool_call_count = state.get("tool_call_count", 0)

        # Nhac nho plan neu co
        if plan:
            done_count     = sum(1 for s in steps if s.get("type") == "tool_call")
            remaining      = plan[done_count:] if done_count < len(plan) else []
            if remaining:
                messages = [*messages, {
                    "role":    "system",
                    "content": f"[Ke hoach con lai] {json.dumps(remaining, ensure_ascii=False)}",
                }]

        # Observation summarization
        if tool_call_count >= _OBSERVATION_SUMMARY_THRESHOLD:
            messages = _summarize_messages(messages)
            print(f"[LOG: REASON NODE] Da tom tat messages (tool_call_count={tool_call_count}).")

        print("[LOG: REASON NODE] Dang cho LLM...")
        answer = self._complete(messages, state["temperature"]).strip()
        print(f"[LOG: REASON NODE] LLM output ({len(answer)} chars):\n{answer}\n" + "-"*40)

        if not answer:
            steps.append({"type": "error", "content": "LLM tra ve rong."})
            return {"iteration": iteration, "steps": steps, "done": True}

        tool_name, tool_params = self._parse(answer)

        if tool_name is None:
            print("[LOG: REASON NODE] Final answer.")
            steps.append({"type": "final_answer", "content": answer})
            return {"iteration": iteration, "steps": steps, "done": True}

        # Extract thinking (text truoc tool_call block)
        think_text = answer
        if "```tool_call" in answer:
            think_text = answer.split("```tool_call")[0].strip()
        elif "{" in answer:
            think_text = answer.split("{")[0].strip()
        if think_text:
            steps.append({"type": "thinking", "content": think_text,
                          "iteration": iteration})

        params = tool_params or {}
        print(f"[LOG: REASON NODE] Tool call: tool='{tool_name}', params={params}")
        steps.append({"type": "tool_call", "tool": tool_name,
                      "params": params, "iteration": iteration})

        updated_messages = list(state["messages"])
        updated_messages.append({"role": "assistant", "content": answer})

        if self._needs_approval(tool_name):
            return {
                "iteration":    iteration,
                "steps":        steps,
                "messages":     updated_messages,
                "pending_tool": {"tool": tool_name, "params": params},
                "done":         True,
            }

        return {
            "iteration":       iteration,
            "steps":           steps,
            "messages":        updated_messages,
            "tool_call_count": tool_call_count,
        }

    @staticmethod
    def _after_reason(state: AgentState) -> str:
        if state["done"] or state["pending_tool"] is not None:
            print("--- [LOG: ROUTER] Ket thuc (hoac cho phe duyet) ---")
            return "end"
        print("--- [LOG: ROUTER] -> Tool Node ---")
        return "tool"

    # ── Node: tool ─────────────────────────────────────────────────────────────

    def _tool(self, state: AgentState) -> dict[str, Any]:
        # Tim tool_call chua co tool_result tuong ung
        call_steps   = [s for s in state["steps"] if s.get("type") == "tool_call"]
        result_steps = [s for s in state["steps"] if s.get("type") == "tool_result"]
        if len(call_steps) > len(result_steps):
            last_step = call_steps[len(result_steps)]
        else:
            last_step = state["steps"][-1]

        tool_name = last_step["tool"]
        params    = last_step["params"]

        print(f"\n--- [LOG: TOOL NODE] Thuc thi: {tool_name} ---")
        try:
            result = self._execute(tool_name, params)
            print(f"[LOG: TOOL NODE] Ket qua: {result}")
        except Exception as error:  # noqa: BLE001
            result = {"error": f"Tool exception: {error}"}
            print(f"[LOG: TOOL NODE] Loi: {result}")

        steps = list(state["steps"])
        steps.append({"type": "tool_result", "tool": tool_name,
                      "result": result, "iteration": state["iteration"]})

        result_text = json.dumps(result, ensure_ascii=False, indent=2)
        if len(result_text) > 8000:
            result_text = result_text[:8000] + "\n... [truncated]"

        # ── FIX: application_action completion signal ─────────────────────────
        # Neu application_action tra ve success=True -> task da xong.
        # Goi LLM mot lan cuoi de sinh cau xac nhan ngan, sau do done=True.
        if tool_name in _COMPLETION_TOOLS and result.get("success"):
            action = result.get("action", tool_name)
            confirm_msgs = list(state["messages"])
            confirm_msgs.append({
                "role":    "user",
                "content": (
                    f"Lenh `{action}` da duoc thuc thi thanh cong boi desktop client. "
                    f"Hay thong bao ngan gon cho nguoi dung bang tieng Viet."
                ),
            })
            final_text = self._complete(confirm_msgs, state["temperature"]).strip()
            if final_text:
                steps.append({"type": "final_answer", "content": final_text,
                              "iteration": state["iteration"]})
            return {"steps": steps, "messages": confirm_msgs, "done": True}

        # ── Tool binh thuong: tiep tuc vong lap ──────────────────────────────
        messages = list(state["messages"])
        messages.append({
            "role":    "user",
            "content": (
                f"Tool `{tool_name}` tra ve:\n```json\n{result_text}\n```\n\n"
                f"Phan tich ket qua va thuc hien buoc tiep theo, "
                f"hoac tra loi cuoi cung neu da du thong tin."
            ),
        })
        return {
            "steps":           steps,
            "messages":        messages,
            "tool_call_count": state.get("tool_call_count", 0) + 1,
        }


# ── Observation Summarization ───────────────────────────────────────────────

def _summarize_messages(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    """Rut gon lich su hoi thoai: giu system + first-user-task,
    tom tat cac tool results cu, giu N messages gan nhat.
    """
    if len(messages) <= 4:
        return messages

    system     = [m for m in messages if m.get("role") == "system"]
    first_user = next((m for m in messages if m.get("role") == "user"), None)
    keep_count = _OBSERVATION_KEEP_LAST * 2
    middle     = messages[2:-keep_count] if len(messages) > 2 + keep_count else []
    recent     = messages[-keep_count:]

    if not middle:
        return messages

    parts = []
    for m in middle:
        role    = m.get("role", "")
        content = m.get("content", "")[:300]
        parts.append(f"[{role}] {content}")

    summary_msg = {
        "role":    "system",
        "content": "[Tom tat cac buoc da thuc hien]\n" + "\n".join(parts) + "\n[Het tom tat]",
    }

    result = list(system)
    if first_user:
        result.append(first_user)
    result.append(summary_msg)
    result.extend(recent)
    return result
