"""LangGraph orchestration for the local 3D-Reconstruction agent.

The graph owns the agent loop; the host application owns model inference and
tool execution.  This keeps Qt-specific actions outside the Python process.

Architecture (ReAct + Plan-and-Execute):
  START -> plan -> plan_reflect -> reason -> tool -> reflect (loop) -> END

  plan   : Sinh ke hoach (danh sach cac buoc) truoc khi bat dau tool loop.
  plan_reflect : Đánh giá kế hoạch một lần; nếu không đạt thì quay lại plan.
  reason : Quan sat ket qua tool truoc do, quyet dinh buoc tiep theo hoac ket thuc.
  tool   : Thuc thi tool duoc chon.

── REASON / REFLECT POLICY (2026-08-27) ─────────────────────────────────────
Each plan step is selected by the LLM.  A desktop-action hint, when available,
is advisory only; it is never a request-specific hard-coded dispatch. Reflect
reviews the actual tool result against the current plan step. A failed review
is appended to the next Reason context so the model can choose a different
valid tool or parameters. Invalid tool/action names are handled by the normal
validation feedback loop.
──────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from langgraph.graph import END, START, StateGraph

from ai_assistant.observability import langsmith_trace, span
from modules.action_manifest import (
    normalize_text,
    rank_actions_for_step,
)
from modules.agent_logging import get_agent_logger
from modules.checkpointing import build_checkpointer
from modules.coding_agent import is_coding_task

_MAX_STEP_MISMATCH_REJECTIONS = 2
logger = get_agent_logger("reasoning")

# ── Import constants & prompts from orchestration package (Pha 4 migration) ──
try:
    from src.ai_assistant.orchestration.state import (
        COMPLETION_TOOLS as _COMPLETION_TOOLS,
    )
    from src.ai_assistant.orchestration.state import (
        CONTEXT_MESSAGE_LIMIT as _CONTEXT_MESSAGE_LIMIT,
    )
    from src.ai_assistant.orchestration.state import (
        CONTEXT_SYSTEM_LIMIT as _CONTEXT_SYSTEM_LIMIT,
    )
    from src.ai_assistant.orchestration.state import (
        CONTEXT_TOTAL_LIMIT as _CONTEXT_TOTAL_LIMIT,
    )
    from src.ai_assistant.orchestration.state import (
        LOW_RISK_TOOLS as _LOW_RISK_TOOLS,
    )
    from src.ai_assistant.orchestration.state import (
        OBSERVATION_KEEP_LAST as _OBSERVATION_KEEP_LAST,
    )
    from src.ai_assistant.orchestration.state import (
        OBSERVATION_SUMMARY_THRESHOLD as _OBSERVATION_SUMMARY_THRESHOLD,
    )
    from src.ai_assistant.orchestration.state import (
        SEMANTIC_REFLECTION_TOOLS as _SEMANTIC_REFLECTION_TOOLS,
    )
except ImportError:
    # Fallback: keep inline definitions (test environments without src/ on PYTHONPATH)
    _COMPLETION_TOOLS = {"application_action"}
    _LOW_RISK_TOOLS = {
        "read_file", "list_directory", "find_files", "search_text", "analyze_code",
        "git_diff", "get_project_status", "validate_file", "rag_search",
    }
    _OBSERVATION_SUMMARY_THRESHOLD = 4
    _OBSERVATION_KEEP_LAST = 10
    _CONTEXT_SYSTEM_LIMIT = 3500
    _CONTEXT_MESSAGE_LIMIT = 4000
    _CONTEXT_TOTAL_LIMIT = 11000
    _SEMANTIC_REFLECTION_TOOLS = {"run_command", "write_file", "patch_file", "replace_file_content", "multi_replace_file_content", "create_directory", "application_action"}




try:
    from src.ai_assistant.orchestration.helpers import (
        is_project_role_lookup,
        plan_step_execution_contract,
        project_role_rag_query,
    )
    from src.ai_assistant.orchestration.nodes.summarize import (
        summarize_messages as _summarize_messages,
    )

    def _project_role_rag_query(text: str) -> str:
        return project_role_rag_query(text, normalize_text)
        
    def _is_project_role_lookup(text: str) -> bool:
        return is_project_role_lookup(text, normalize_text)
        
    def _plan_step_execution_contract(step_text: str) -> dict[str, Any]:
        return plan_step_execution_contract(step_text, rank_actions_for_step, normalize_text)
except ImportError:
    pass # In actual environment, these are guaranteed to be available due to the module structure.
    # We will leave the fallback out to reduce bloat, since tests will use the proper path or mock it.
    
try:
    from src.ai_assistant.orchestration.state import (
        AgentState,
        Completion,
        Executor,
        NeedsApproval,
        Parser,
        ReflectResult,
        SelectSpecialist,
        VerifyResult,
    )
except ImportError:
    pass # Managed by the try/except block above

class LocalAgentGraph:
    """ReAct + Plan-and-Execute graph cho local llama.cpp model."""

    def __init__(self, complete: Completion, parse: Parser, execute: Executor,
                 needs_approval: NeedsApproval, max_iterations: int,
                 emit: Callable[[dict[str, Any]], None] | None = None,
                 select_specialist: SelectSpecialist | None = None,
                 verify_result: VerifyResult | None = None,
                 reflect_result: ReflectResult | None = None,
                 plan_complete: Completion | None = None,
                 reflect_complete: Completion | None = None,
                 plan_reflect_complete: Completion | None = None,
                 cancel_checker: Callable[[], bool] | None = None) -> None:
        self._complete       = complete
        self._parse          = parse
        self._execute        = execute
        self._needs_approval = needs_approval
        self._max_iterations = max_iterations
        self._emit = emit
        self._select_specialist = select_specialist
        self._verify_result = verify_result
        self._reflect_result = reflect_result
        # Planner and critic require JSON schemas different from the tool/final
        # envelope used by the ReAct reasoner.
        self._plan_complete = plan_complete or complete
        self._reflect_complete = reflect_complete or complete
        # Kept separate so callers/tests can provide independent planner and
        # plan-review completions. If omitted, deterministic checks still run
        # and a plan is accepted without consuming the tool-critic callback.
        self._plan_reflect_complete = plan_reflect_complete
        self._cancel_checker = cancel_checker or (lambda: False)
        self._emitted_steps = 0

        builder = StateGraph(AgentState)
        builder.add_node("plan",   self._traced("plan", self._plan))
        builder.add_node("plan_reflect", self._traced("plan_reflect", self._plan_reflect))
        builder.add_node("reason", self._traced("reason", self._reason))
        builder.add_node("tool",   self._traced("tool", self._tool))
        builder.add_node("reflect", self._traced("reflect", self._reflect))

        # A resumed UI workflow already has an ACK result. Review that result
        # before reasoning again, rather than planning or dispatching another action.
        builder.add_conditional_edges(START, self._initial_node,
                                      {"plan": "plan", "reflect": "reflect"})
        builder.add_conditional_edges("plan", self._after_plan,
                                      {"plan_reflect": "plan_reflect", "reason": "reason"})
        builder.add_conditional_edges("plan_reflect", self._after_plan_reflect,
                                      {"plan": "plan", "reason": "reason", "end": END})
        builder.add_conditional_edges("reason", self._after_reason,
                                      {"tool": "tool", "reason": "reason", "end": END})
        # A tool normally returns to the reasoning loop.  UI actions and
        # approval-gated tools instead set ``done``/``pending_tool`` and must
        # stop immediately: their result is completed asynchronously by the
        # desktop client.  An unconditional edge here would call the model
        # again and dispatch a second desktop action before the first ACK.
        builder.add_conditional_edges("tool", self._after_tool,
                                      {"reason": "reason", "reflect": "reflect", "end": END})
        builder.add_conditional_edges("reflect", self._after_reflect,
                                      {"reason": "reason", "end": END})

        self._graph = builder.compile(checkpointer=build_checkpointer())

    def _traced(self, name: str, handler: Callable[[AgentState], dict[str, Any]]) -> Callable[[AgentState], dict[str, Any]]:
        def invoke(state: AgentState) -> dict[str, Any]:
            iteration = state.get("iteration", 0)
            latest_delegation = next(
                (step for step in reversed(state.get("steps", []))
                 if step.get("type") == "delegation"),
                {},
            )
            specialist = latest_delegation.get("agent", state.get("routing_plan", "supervisor"))
            tool_name = latest_delegation.get("tool")
            trace_metadata = {
                "node": name,
                "iteration": iteration,
                "specialist": specialist,
                "tool_name": tool_name,
            }
            with span(f"agent.{name}", **trace_metadata):
                with langsmith_trace(
                    f"agent.{name}",
                    run_type="chain",
                    inputs={"iteration": iteration,
                            "routing_plan": state.get("routing_plan", ""),
                            "done": state.get("done", False),
                            "specialist": specialist,
                            "tool_name": tool_name},
                    metadata=trace_metadata,
                ) as ls_ctx:
                    result = handler(state)
                    ls_ctx["outputs"] = {
                        "done": result.get("done", False),
                        "step_count": len(result.get("steps", [])),
                    }
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
            iteration: int = 0, resume_with_reflection: bool = False,
            required_ui_actions: list[dict[str, Any]] | None = None,
            supervisor_route: str | None = None,
            enforce_plan_completion: bool = False,
            approval_granted: bool = False,
            approval_scope: str = "") -> AgentState:
        self._emitted_steps = len(steps or [])
        restored_plan = next(
            (step.get("steps") for step in reversed(steps or []) if step.get("type") == "plan"),
            None,
        )
        prior_plan_review = next(
            (step.get("result", {}) for step in reversed(steps or [])
             if step.get("type") == "plan_reflection"), None)

        config = {"configurable": {"thread_id": session_id}}

        input_state: dict[str, Any] = {
            "messages":        messages,
            "steps":           steps or [],
            "iteration":       iteration,
            "temperature":     temperature,
            "done":            False,
            "pending_tool":    None,
            "plan":            restored_plan,
            "plan_spec":       next((step.get("spec") for step in reversed(steps or [])
                                      if step.get("type") == "plan"), None),
            "approval_granted": approval_granted,
            "approval_scope":   approval_scope,
            "cancelled":        False,
            "tool_call_count": 0,
            "last_reflection": None,
            "error_count":     0,
            "resume_with_reflection": resume_with_reflection,
            "skip_reflect":    False,
            "synthesize_after_rag": False,
            "plan_verified":   bool(prior_plan_review and prior_plan_review.get("passed") is True),
            "plan_attempts":   sum(1 for step in (steps or []) if step.get("type") == "plan"),
            "plan_feedback":   "",
            "routing_plan":    supervisor_route,
            "enforce_plan_completion": enforce_plan_completion,
            "enforce_coding_workflow": is_coding_task(next((m.get("content", "") for m in messages if m.get("role") == "user"), "")),
        }

        # FIX: `required_ui_actions` used to be overwritten with `[]` on every
        # call unless the host explicitly re-passed it — including on the
        # resume-after-UI-ACK call that re-enters at `reflect`. That silently
        # wiped the UI plan's routing gate mid-flow, which made `_reflect`
        # fall back to the full critic evaluation (scored against the WHOLE
        # original request) instead of the completed-bypass path, and made
        # `_reason`'s routing gate forget which step came next. Only reset to
        # `[]` for a genuinely fresh task; on resume, if the host doesn't
        # supply the list, recover it from the last checkpoint instead of
        # dropping it.
        if required_ui_actions is not None:
            input_state["required_ui_actions"] = required_ui_actions
        elif not resume_with_reflection:
            input_state["required_ui_actions"] = []
        else:
            try:
                prior = self._graph.get_state(config)
                prior_values = prior.values if prior else {}
            except Exception as error:  # noqa: BLE001
                logger.warning(
                    "[run] Không thể đọc checkpoint trước đó để khôi phục "
                    "required_ui_actions (session=%s): %s", session_id, error,
                )
                prior_values = {}
            input_state["required_ui_actions"] = prior_values.get("required_ui_actions", [])
            logger.info(
                "[run] Khôi phục required_ui_actions từ checkpoint (session=%s): %s",
                session_id, input_state["required_ui_actions"],
            )

        return self._graph.invoke(input_state, config=config)

    @staticmethod
    def _initial_node(state: AgentState) -> str:
        """Review a completed asynchronous action before reasoning again."""
        return "reflect" if state.get("resume_with_reflection") else "plan"

    @staticmethod
    def _after_plan(state: AgentState) -> str:
        if state.get("plan_verified") or not state.get("plan"):
            logger.info("[ROUTER: after_plan] → REASON (plan already verified/no plan)")
            return "reason"
        logger.info("[ROUTER: after_plan] → PLAN_REFLECT")
        return "plan_reflect"

    # ── Node: plan ─────────────────────────────────────────────────────────────

    def _plan(self, state: AgentState) -> dict[str, Any]:
        try:
            from src.ai_assistant.orchestration.nodes.plan import plan_node
            return plan_node(state, self._plan_complete)
        except ImportError:
            return {"plan": None}

    # ── Node: plan_reflect ───────────────────────────────────────────────────

    def _plan_reflect(self, state: AgentState) -> dict[str, Any]:
        try:
            from src.ai_assistant.orchestration.nodes.plan_reflect import plan_reflect_node
            return plan_reflect_node(state, self._plan_reflect_complete)
        except ImportError:
            return {"plan_verified": True}

    @staticmethod
    def _after_plan_reflect(state: AgentState) -> str:
        try:
            from src.ai_assistant.orchestration.nodes.plan_reflect import after_plan_reflect
            return after_plan_reflect(state)
        except ImportError:
            return "reason"

    # ── Node: reason ───────────────────────────────────────────────────────────

    def _reason(self, state: AgentState) -> dict[str, Any]:
        try:
            from src.ai_assistant.orchestration.nodes.reason import ReasonContext, reason_node
            ctx = ReasonContext(
                complete=self._complete,
                parse=self._parse,
                needs_approval=self._needs_approval,
                max_iterations=self._max_iterations,
                cancel_checker=self._cancel_checker,
                select_specialist=self._select_specialist
            )
            return reason_node(state, ctx)
        except ImportError as e:
            logger.error("Failed to import reason_node: %s", e)
            return {"iteration": state.get("iteration", 0) + 1, "done": True, "steps": state.get("steps", [])}

    @staticmethod
    def _after_reason(state: AgentState) -> str:
        try:
            from src.ai_assistant.orchestration.nodes.reason import after_reason
            return after_reason(state)
        except ImportError:
            return "end"

    # ── Node: tool ─────────────────────────────────────────────────────────────

    def _tool(self, state: AgentState) -> dict[str, Any]:
        logger.info("[NODE: tool] Bắt đầu thực thi tool.")
        if self._cancel_checker():
            steps = list(state["steps"])
            steps.append({"type": "cancelled", "content": "Task cancelled cooperatively.",
                          "iteration": state.get("iteration", 0)})
            return {"steps": steps, "done": True, "cancelled": True}
        # Tim tool_call chua co tool_result tuong ung
        call_steps   = [s for s in state["steps"] if s.get("type") == "tool_call"]
        result_steps = [s for s in state["steps"] if s.get("type") == "tool_result"]
        if len(call_steps) <= len(result_steps):
            logger.error("[NODE: tool] Reached without a pending tool_call.")
            steps = list(state["steps"])
            steps.append({
                "type": "error",
                "content": "Tool node reached without a pending tool call.",
                "iteration": state.get("iteration", 0),
            })
            return {"steps": steps, "done": True}

        last_step = call_steps[len(result_steps)]

        tool_name = last_step.get("tool")
        params = last_step.get("params")
        if not tool_name or not isinstance(params, dict):
            logger.error("[NODE: tool] Pending tool_call has invalid payload: %s", last_step)
            steps = list(state["steps"])
            steps.append({
                "type": "error",
                "content": "Pending tool call has an invalid payload.",
                "iteration": state.get("iteration", 0),
            })
            return {"steps": steps, "done": True}

        is_project_research = (tool_name == "rag_search")

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

        # Desktop actions are executed by Qt, outside this process.  Stop the
        # graph until the client explicitly acknowledges the request instead
        # of treating dispatch as success.
        if result.get("pending_ui_ack"):
            return {
                "steps": state.get("steps", []),
                "pending_tool": {"tool": tool_name, "params": params, "ui_ack": True},
                "done": True,
            }

        steps = list(state["steps"])
        tool_result_entry: dict[str, Any] = {
            "type": "tool_result", "tool": tool_name,
            "result": result, "iteration": state["iteration"],
        }
        if isinstance(last_step.get("plan_step_index"), int):
            tool_result_entry["plan_step_index"] = last_step["plan_step_index"]
        steps.append(tool_result_entry)

        if error_count >= 3:
            steps.append({"type": "final_answer", "content": f"Ngắt mạch (Circuit Breaker): Tool '{tool_name}' gặp lỗi 3 lần liên tiếp. Dừng tác vụ để tránh vòng lặp."})
            return {"steps": steps, "done": True, "error_count": error_count}
        verification = {"passed": "error" not in result, "reason": "No verifier configured."}
        if self._verify_result:
            verification = self._verify_result(tool_name, params, result)
            steps.append({"type": "verification", "tool": tool_name, "result": verification,
                          "iteration": state["iteration"]})

        if is_project_research:
            # RAG returns context decorated with headings and source labels.
            # Keep verified evidence but ask the LLM to synthesize a step_answer
            # immediately so plan progress advances without an extra round-trip.
            # Cap evidence and compact messages to stay within the local model's
            # context window (llama.cpp models have limited token capacity).
            evidence = json.dumps(result, ensure_ascii=False, indent=2)
        # Role answers often need distinct passages for identity, role and
            # responsibilities, so retain a larger verified evidence window.
            evidence_limit = 6000
            evidence_snippet = evidence[:evidence_limit] + ("\n... [truncated]" if len(evidence) > evidence_limit else "")

            # Determine which plan step this RAG call was serving.
            rag_plan_step = ""
            if isinstance(last_step.get("plan_step_index"), int):
                rag_step_idx = last_step["plan_step_index"]
                plan_now = state.get("plan") or []
                if 0 <= rag_step_idx < len(plan_now):
                    rag_plan_step = plan_now[rag_step_idx]
            step_directive = (
                f' Trả lời ngắn gọn ĐÚNG bước: "{rag_plan_step}"'
                " — không lặp lại ý đã nêu ở các bước trước."
                if rag_plan_step else ""
            )
            role_answer_directive = (
                " Chỉ nêu thông tin trong phạm vi bước hiện tại; KHÔNG lặp lại nội dung"
                " đã trả lời ở các bước trước. Trình bày tự nhiên, mạch lạc như đang giải"
                " thích cho người dùng; KHÔNG ép câu trả lời vào khung mục"
                " 'Thông tin / Vai trò / Nhiệm vụ-Trách nhiệm', và nếu thiếu dữ liệu thì"
                " nói rõ dữ liệu chưa có thay vì bịa đặt."
            )

            messages = _summarize_messages(list(state["messages"]))
            messages.append({
                "role": "user",
                "content": (
                    "RAG evidence đã được xác minh." + step_directive + role_answer_directive
                    + " Dùng bằng chứng dưới đây để trả lời ngắn gọn, tự nhiên."
                    " KHÔNG đề cập tiêu đề nguồn, tên file, số trích dẫn,"
                    " 'TÀI LIỆU THAM KHẢO' hay 'MÃ NGUỒN LIÊN QUAN'."
                    ' Trả về {"kind":"step_answer","content":"..."} — KHÔNG dùng final.\n\n'
                    f"Bằng chứng:\n```json\n{evidence_snippet}\n```"
                ),
            })
            # Evidence is not a completed plan step.  Returning to Reason
            # without a reflection keeps this same step active, so the model
            # can synthesize the user-facing answer before progressing.
            logger.info("[NODE: tool] Project RAG evidence ready; requesting step_answer synthesis.")
            return {"steps": steps, "messages": messages,
                    "tool_call_count": state.get("tool_call_count", 0) + 1,
                    "error_count": error_count, "synthesize_after_rag": True}


        result_text = json.dumps(result, ensure_ascii=False, indent=2)
        if len(result_text) > 8000:
            result_text = result_text[:8000] + "\n... [truncated]"

        # ── COMPLETION_TOOLS: đánh dấu hoàn thành nhưng vẫn đi qua Reflect ────────
        # Không sinh final_answer tại đây — để Reflect Critic xác nhận rồi Reason mới kết thúc.
        if tool_name in _COMPLETION_TOOLS and result.get("success"):
            messages = list(state["messages"])
            messages.append({
                "role":    "user",
                "content": (
                    f"Tool `{tool_name}` đã thực thi thành công (action: {result.get('action', tool_name)}). "
                    f"Hãy thông báo kết quả ngắn gọn cho người dùng."
                ),
            })
            return {
                "steps":            steps,
                "messages":         messages,
                "tool_call_count":  state.get("tool_call_count", 0) + 1,
                "error_count":      error_count,
            }

        # ── Tool binh thuong: tiep tuc vong lap ──────────────────────────────
        messages = list(state["messages"])
        messages.append({
            "role":    "user",
            "content": (
                f"Tool `{tool_name}` tra ve:\n```json\n{result_text}\n```\n\n"
                "Use this result as evidence. If it answers the original question, "
                "return a final answer now; do not delegate or call another tool. "
                "Only continue when a specific missing fact is necessary."
            ),
        })
        if tool_name in _LOW_RISK_TOOLS and verification.get("passed") and "error" not in result:
            # Read-only tools cannot change application state. Record a passed
            # reflection for plan accounting without paying for a critic LLM call.
            steps.append({
                "type": "reflection", "tool": tool_name,
                "result": {"passed": True, "decision": "continue",
                            "reason": "Verified low-risk read-only tool result."},
                "iteration": state["iteration"],
            })
            if isinstance(last_step.get("plan_step_index"), int):
                steps[-1]["plan_step_index"] = last_step["plan_step_index"]
            return {
                "steps":           steps,
                "messages":        messages,
                "tool_call_count": state.get("tool_call_count", 0) + 1,
                "error_count":     error_count,
                "skip_reflect":    True,
            }
        if (tool_name not in _SEMANTIC_REFLECTION_TOOLS
                and verification.get("passed") and "error" not in result):
            steps.append({
                "type": "reflection", "tool": tool_name,
                "result": {"passed": True, "decision": "continue",
                            "reason": "Verified result contract; semantic critic not required."},
                "iteration": state["iteration"],
            })
            if isinstance(last_step.get("plan_step_index"), int):
                steps[-1]["plan_step_index"] = last_step["plan_step_index"]
            return {"steps": steps, "messages": messages,
                    "tool_call_count": state.get("tool_call_count", 0) + 1,
                    "error_count": error_count, "skip_reflect": True}
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
        if state.get("synthesize_after_rag"):
            logger.info("[ROUTER: after_tool] → REASON (synthesize verified RAG evidence)")
            return "reason"
        if (state.get("skip_reflect") and state.get("steps")
                and state["steps"][-1].get("type") == "reflection"):
            logger.info("[ROUTER: after_tool] → REASON (verified low-risk tool)")
            return "reason"
        logger.info("[ROUTER: after_tool] → REFLECT")
        print("--- [LOG: TOOL ROUTER] -> Reflect Node ---")
        return "reflect"

    # ── Node: reflect ────────────────────────────────────────────────────────

    def _reflect(self, state: AgentState) -> dict[str, Any]:
        try:
            from src.ai_assistant.orchestration.nodes.reflect import ReflectContext, reflect_node
            ctx = ReflectContext(
                reflect_complete=self._reflect_complete,
                reflect_result=self._reflect_result
            )
            return reflect_node(state, ctx)
        except ImportError as e:
            logger.error("Failed to import reflect_node: %s", e)
            return {}

    @staticmethod
    def _after_reflect(state: AgentState) -> str:
        try:
            from src.ai_assistant.orchestration.nodes.reflect import after_reflect
            return after_reflect(state)
        except ImportError:
            return "reason"

