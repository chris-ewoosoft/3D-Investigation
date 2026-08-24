"""LangGraph orchestration for the local 3D-Reconstruction agent.

The graph owns the agent loop; the host application owns model inference and
tool execution.  This keeps Qt-specific actions outside the Python process.

Architecture (ReAct + Plan-and-Execute):
  START -> plan -> reason -> tool -> reflect (loop) -> END

  plan   : Sinh ke hoach (danh sach cac buoc) truoc khi bat dau tool loop.
  reason : Quan sat ket qua tool truoc do, quyet dinh buoc tiep theo hoac ket thuc.
  tool   : Thuc thi tool duoc chon.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from modules.checkpointing import build_checkpointer
from modules.observability import span

logger = logging.getLogger("langgraph_agent")

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
    last_reflection: dict[str, Any] | None  # deterministic critic result
    error_count:     int                    # so lan tool loi lien tiep


Completion    = Callable[[list[dict[str, str]], float], str]
Parser        = Callable[[str], tuple[str | None, dict[str, Any] | None]]
Executor      = Callable[[str, dict[str, Any]], dict[str, Any]]
NeedsApproval = Callable[[str], bool]
SelectSpecialist = Callable[[str, dict[str, Any]], dict[str, Any]]
VerifyResult = Callable[[str, dict[str, Any], dict[str, Any]], dict[str, Any]]
ReflectResult = Callable[[str, dict[str, Any], dict[str, Any], dict[str, Any]], dict[str, Any]]


class LocalAgentGraph:
    """ReAct + Plan-and-Execute graph cho local llama.cpp model."""

    def __init__(self, complete: Completion, parse: Parser, execute: Executor,
                 needs_approval: NeedsApproval, max_iterations: int,
                 emit: Callable[[dict[str, Any]], None] | None = None,
                 select_specialist: SelectSpecialist | None = None,
                 verify_result: VerifyResult | None = None,
                 reflect_result: ReflectResult | None = None) -> None:
        self._complete       = complete
        self._parse          = parse
        self._execute        = execute
        self._needs_approval = needs_approval
        self._max_iterations = max_iterations
        self._emit = emit
        self._select_specialist = select_specialist
        self._verify_result = verify_result
        self._reflect_result = reflect_result
        self._emitted_steps = 0

        builder = StateGraph(AgentState)
        builder.add_node("plan",   self._traced("plan", self._plan))
        builder.add_node("reason", self._traced("reason", self._reason))
        builder.add_node("tool",   self._traced("tool", self._tool))
        builder.add_node("reflect", self._traced("reflect", self._reflect))

        builder.add_edge(START, "plan")
        builder.add_conditional_edges("plan", self._after_plan,
                                      {"reason": "reason", "end": END})
        builder.add_conditional_edges("reason", self._after_reason,
                                      {"tool": "tool", "reason": "reason", "end": END})
        # A tool normally returns to the reasoning loop.  UI actions and
        # approval-gated tools instead set ``done``/``pending_tool`` and must
        # stop immediately: their result is completed asynchronously by the
        # desktop client.  An unconditional edge here would call the model
        # again and dispatch a second desktop action before the first ACK.
        builder.add_conditional_edges("tool", self._after_tool,
                                      {"reflect": "reflect", "end": END})
        builder.add_conditional_edges("reflect", self._after_reflect,
                                      {"reason": "reason", "end": END})

        self._graph = builder.compile(checkpointer=build_checkpointer())

    def _traced(self, name: str, handler: Callable[[AgentState], dict[str, Any]]) -> Callable[[AgentState], dict[str, Any]]:
        def invoke(state: AgentState) -> dict[str, Any]:
            with span(f"agent.{name}", iteration=str(state.get("iteration", 0))):
                result = handler(state)
            if self._emit and result.get("steps"):
                steps = result["steps"]
                for step in steps[self._emitted_steps:]:
                    self._emit(step)
                self._emitted_steps = len(steps)
            return result
        return invoke

    # ── Public API ─────────────────────────────────────────────────────────────

    def run(self, messages: list[dict[str, str]], session_id: str,
            temperature: float, steps: list[dict[str, Any]] | None = None,
            iteration: int = 0) -> AgentState:
        self._emitted_steps = len(steps or [])
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
                "last_reflection": None,
                "error_count":     0,
            },
            config={"configurable": {"thread_id": session_id}},
        )

    # ── Node: plan ─────────────────────────────────────────────────────────────

    def _plan(self, state: AgentState) -> dict[str, Any]:
        """Sinh ke hoach cac buoc. Skip neu cau ngan (likely UI action)."""
        logger.info("[NODE: plan] Bắt đầu sinh kế hoạch.")
        logger.debug(f"[NODE: plan] State hiện tại (iteration: {state.get('iteration')}): messages={len(state.get('messages', []))} steps={len(state.get('steps', []))}")
        messages = state["messages"]
        
        for step in state.get("steps", []):
            if step.get("type") == "plan":
                logger.info(f"[NODE: plan] Đã có kế hoạch từ trước: {step.get('steps')}")
                return {"plan": step.get("steps")}
                
        user_msg = messages[-1].get("content", "") if messages else ""

        user_msg_lower = user_msg.lower()
        has_multiple_intent = any(kw in user_msg_lower for kw in [" và ", " rồi ", " sau đó ", " and ", " then "])
        
        # Skip LLM planning cho cac cau ngan hoac UI actions don gian
        if len(user_msg) < 80 and not has_multiple_intent:
            print("[LOG: PLAN NODE] Cau ngan, skip LLM planning.")
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
        print("[AGENT TRACE] ▶ Plan node: đang sinh kế hoạch...", flush=True)
        raw = self._complete(planning_msgs, max(0.1, state["temperature"] - 0.1)).strip()
        print(f"[AGENT TRACE] ── Plan: kế hoạch thô: {raw[:200]}", flush=True)

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
            print(f"[AGENT TRACE] ── Plan: {plan}", flush=True)
            logger.info(f"[NODE: plan] Đã sinh kế hoạch: {plan}")
            return {"plan": plan, "steps": steps}

        print("[AGENT TRACE] ── Plan: không parse được, tiếp tục không có plan.", flush=True)
        logger.info("[NODE: plan] Không sinh được kế hoạch hoặc bỏ qua.")
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
        print(f"\n[AGENT TRACE] ── Reason node: iter {iteration}", flush=True)
        logger.info(f"[NODE: reason] Bắt đầu suy luận (iteration {iteration})")

        if iteration > self._max_iterations:
            print(f"[AGENT TRACE] ── Reason: đạt giới hạn ({self._max_iterations} iterations).", flush=True)
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
            print(f"[AGENT TRACE] ── Reason: tóm tắt messages (tool_call_count={tool_call_count}).", flush=True)

        print("[AGENT TRACE] ── Reason: đang gọi LLM...", flush=True)
        logger.debug(f"[NODE: reason] Tin nhắn gửi đến LLM: {json.dumps(messages, ensure_ascii=False)}")
        answer = self._complete(messages, state["temperature"]).strip()
        print(f"[AGENT TRACE] ── Reason: LLM output ({len(answer)} chars): {answer[:120].replace(chr(10), ' ')}", flush=True)
        logger.info(f"[NODE: reason] LLM trả về raw answer: {answer}")

        if not answer:
            steps.append({"type": "error", "content": "LLM tra ve rong."})
            return {"iteration": iteration, "steps": steps, "done": True}

        tool_name, tool_params = self._parse(answer)

        if tool_name is None:
            print("[AGENT TRACE] ── Reason: final answer.", flush=True)
            logger.info("[NODE: reason] LLM quyết định dừng (final_answer).")
            steps.append({"type": "final_answer", "content": answer})
            return {"iteration": iteration, "steps": steps, "done": True}

        # ── Validation error: xử lý ngay, không gửi sang tool node ────────
        # Khi _parse trả về _validation_error, nghĩa là LLM đã gọi tool với
        # tham số không hợp lệ. Thay vì chạy qua tool→reflect, ta đưa lỗi
        # trực tiếp vào messages để LLM tự sửa ở lượt suy luận tiếp.
        if tool_name == "_validation_error":
            error_detail = (tool_params or {}).get("error", "Unknown validation error")
            original_tool = (tool_params or {}).get("tool", "unknown")
            logger.warning(
                "[NODE: reason] Validation error cho tool '%s': %s",
                original_tool, error_detail,
            )
            print(
                f"[AGENT TRACE] ── Reason: VALIDATION ERROR cho '{original_tool}': {error_detail}",
                flush=True,
            )
            steps.append({
                "type": "validation_error", "tool": original_tool,
                "error": error_detail, "iteration": iteration,
            })
            updated_messages = list(state["messages"])
            updated_messages.append({"role": "assistant", "content": answer})
            updated_messages.append({
                "role": "user",
                "content": (
                    f"LỖI: Bạn vừa gọi tool '{original_tool}' nhưng tham số KHÔNG HỢP LỆ.\n"
                    f"Chi tiết: {error_detail}\n\n"
                    f"Hãy đọc danh sách action hợp lệ ở trên và gọi lại "
                    f"application_action với đúng tên action. "
                    f"KHÔNG ĐƯỢC trả lời bằng văn bản — chỉ trả về JSON tool_call."
                ),
            })
            return {
                "iteration":       iteration,
                "steps":           steps,
                "messages":        updated_messages,
                "tool_call_count": tool_call_count,
            }

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
        if self._select_specialist:
            delegation = self._select_specialist(tool_name, params)
            steps.append({"type": "delegation", "agent": delegation.get("specialist", "supervisor"),
                          "tool": tool_name, "idempotency_key": delegation.get("idempotency_key", ""),
                          "iteration": iteration})
        print(f"[AGENT TRACE] ── Reason: tool call → tool='{tool_name}', params={str(params)[:80]}", flush=True)
        logger.info("[NODE: reason] LLM quyết định gọi tool: %s, params: %s", tool_name, params)
        steps.append({"type": "tool_call", "tool": tool_name,
                      "params": params, "iteration": iteration})

        updated_messages = list(state["messages"])
        updated_messages.append({"role": "assistant", "content": answer})
        if self._select_specialist and delegation.get("instruction"):
            updated_messages.append({
                "role": "system",
                "content": f"[Specialist handoff: {delegation.get('specialist')}] {delegation['instruction']}",
            })

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
            logger.info("[ROUTER: after_reason] → END (done=%s, pending=%s)", state["done"], state["pending_tool"] is not None)
            print("[AGENT TRACE] ── Router: kết thúc (hoặc chờ phê duyệt)", flush=True)
            return "end"
        # Validation error: quay lại reason để LLM tự sửa (không qua tool)
        steps = state.get("steps", [])
        if steps and steps[-1].get("type") == "validation_error":
            logger.info("[ROUTER: after_reason] → REASON (validation error, self-correct)")
            print("[AGENT TRACE] ── Router: → Reason node (validation error self-correct)", flush=True)
            return "reason"
        logger.info("[ROUTER: after_reason] → TOOL")
        print("[AGENT TRACE] ── Router: → Tool node", flush=True)
        return "tool"

    # ── Node: tool ─────────────────────────────────────────────────────────────

    def _tool(self, state: AgentState) -> dict[str, Any]:
        logger.info("[NODE: tool] Bắt đầu thực thi tool.")
        # Tim tool_call chua co tool_result tuong ung
        call_steps   = [s for s in state["steps"] if s.get("type") == "tool_call"]
        result_steps = [s for s in state["steps"] if s.get("type") == "tool_result"]
        if len(call_steps) > len(result_steps):
            last_step = call_steps[len(result_steps)]
        else:
            last_step = state["steps"][-1]

        tool_name = last_step["tool"]
        params    = last_step["params"]

        logger.info("[NODE: tool] Thực thi: %s, params: %s", tool_name, str(params)[:200])
        print(f"\n--- [LOG: TOOL NODE] Thuc thi: {tool_name} ---")
        try:
            result = self._execute(tool_name, params)
            logger.info("[NODE: tool] Kết quả: %s", str(result)[:500])
            print(f"[LOG: TOOL NODE] Ket qua: {result}")
        except Exception as error:  # noqa: BLE001
            result = {"error": f"Tool exception: {error}"}
            logger.error("[NODE: tool] Exception khi thực thi '%s': %s", tool_name, error)
            print(f"[LOG: TOOL NODE] Loi: {result}")

        error_count = state.get("error_count", 0)
        if "error" in result:
            error_count += 1
        else:
            error_count = 0

        steps = list(state["steps"])
        steps.append({"type": "tool_result", "tool": tool_name,
                      "result": result, "iteration": state["iteration"]})

        if error_count >= 3:
            steps.append({"type": "final_answer", "content": f"Ngắt mạch (Circuit Breaker): Tool '{tool_name}' gặp lỗi 3 lần liên tiếp. Dừng tác vụ để tránh vòng lặp."})
            return {"steps": steps, "done": True, "error_count": error_count}
        if self._verify_result:
            verification = self._verify_result(tool_name, params, result)
            steps.append({"type": "verification", "tool": tool_name, "result": verification,
                          "iteration": state["iteration"]})

        # Desktop actions are executed by Qt, outside this process.  Stop the
        # graph until the client explicitly acknowledges the request instead
        # of treating dispatch as success.
        if result.get("pending_ui_ack"):
            return {
                "steps": steps,
                "pending_tool": {"tool": tool_name, "params": params, "ui_ack": True},
                "done": True,
            }

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
            "error_count":     error_count,
        }

    @staticmethod
    def _after_tool(state: AgentState) -> str:
        """End after an asynchronous or approval-gated tool result."""
        if state["done"] or state["pending_tool"] is not None:
            logger.info("[ROUTER: after_tool] → END (done=%s, pending=%s)", state["done"], state["pending_tool"] is not None)
            print("--- [LOG: TOOL ROUTER] Ket thuc (cho ACK/phe duyet) ---")
            return "end"
        logger.info("[ROUTER: after_tool] → REFLECT")
        print("--- [LOG: TOOL ROUTER] -> Reflect Node ---")
        return "reflect"

    # ── Node: reflect ────────────────────────────────────────────────────────

    def _reflect(self, state: AgentState) -> dict[str, Any]:
        """Run an independent, no-LLM review before the next ReAct turn."""
        logger.info("[NODE: reflect] Bắt đầu phản ánh kết quả tool.")
        calls = [step for step in state["steps"] if step.get("type") == "tool_call"]
        results = [step for step in state["steps"] if step.get("type") == "tool_result"]
        if not calls or not results:
            logger.info("[NODE: reflect] Không có tool call/result để phản ánh.")
            return {}
        call, result_step = calls[-1], results[-1]
        logger.info("[NODE: reflect] Đánh giá tool='%s'", call["tool"])
        verification = next((step.get("result", {}) for step in reversed(state["steps"])
                             if step.get("type") == "verification" and step.get("tool") == call["tool"]),
                            {"passed": "error" not in result_step.get("result", {})})
        if self._reflect_result:
            reflection = self._reflect_result(call["tool"], call["params"], result_step["result"], verification)
        else:
            reflection = {"passed": bool(verification.get("passed")), "decision": "continue",
                          "reason": verification.get("reason", "No critic configured.")}
        logger.info("[NODE: reflect] Kết quả phản ánh: passed=%s, reason=%s",
                    reflection.get("passed"), reflection.get("reason", "")[:200])
        steps = list(state["steps"])
        steps.append({"type": "reflection", "tool": call["tool"], "result": reflection,
                      "iteration": state["iteration"]})
        messages = list(state["messages"])
        if not reflection.get("passed"):
            logger.warning("[NODE: reflect] Review FAILED cho tool '%s': %s",
                           call["tool"], reflection.get("reason", ""))
            messages.append({"role": "system", "content": (
                "[Independent review failed] " + str(reflection.get("reason", "Revise the approach.")) +
                " Do not repeat the same failing call; inspect evidence or choose a safer next step.")})
        return {"steps": steps, "messages": messages, "last_reflection": reflection}

    @staticmethod
    def _after_reflect(state: AgentState) -> str:
        if state["done"] or state["pending_tool"] is not None:
            logger.info("[ROUTER: after_reflect] → END")
            return "end"
        logger.info("[ROUTER: after_reflect] → REASON")
        return "reason"

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
