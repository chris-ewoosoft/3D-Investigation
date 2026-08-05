"""LangGraph orchestration for the local 3D-Reconstruction agent.

The graph owns the agent loop; the host application owns model inference and
tool execution.  This keeps Qt-specific actions outside the Python process.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph


class AgentState(TypedDict):
    messages: list[dict[str, str]]
    steps: list[dict[str, Any]]
    iteration: int
    temperature: float
    done: bool
    pending_tool: dict[str, Any] | None


Completion = Callable[[list[dict[str, str]], float], str]
Parser = Callable[[str], tuple[str | None, dict[str, Any] | None]]
Executor = Callable[[str, dict[str, Any]], dict[str, Any]]
NeedsApproval = Callable[[str], bool]


class LocalAgentGraph:
    """A small, inspectable ReAct graph for a local llama.cpp model."""

    def __init__(self, complete: Completion, parse: Parser, execute: Executor,
                 needs_approval: NeedsApproval, max_iterations: int) -> None:
        self._complete = complete
        self._parse = parse
        self._execute = execute
        self._needs_approval = needs_approval
        self._max_iterations = max_iterations

        builder = StateGraph(AgentState)
        builder.add_node("reason", self._reason) # Node để model sinh ra câu trả lời và tool_call
        builder.add_node("tool", self._tool) # Node để thực thi tool
        builder.add_edge(START, "reason") # Bắt đầu từ node reason
        builder.add_conditional_edges("reason", self._after_reason, # Điều kiện để chuyển sang tool hoặc end
                                      {"tool": "tool", "end": END})
        builder.add_edge("tool", "reason") # Sau khi thực thi tool thì quay lại reason
        self._graph = builder.compile(checkpointer=MemorySaver())

    def run(self, messages: list[dict[str, str]], session_id: str,
            temperature: float, steps: list[dict[str, Any]] | None = None,
            iteration: int = 0) -> AgentState:
        return self._graph.invoke(
            {
                "messages": messages,
                "steps": steps or [],
                "iteration": iteration,
                "temperature": temperature,
                "done": False,
                "pending_tool": None,
            },
            config={"configurable": {"thread_id": session_id}},
        )

    def _reason(self, state: AgentState) -> dict[str, Any]:
        iteration = state["iteration"] + 1
        steps = list(state["steps"])
        print(f"\n--- [LOG: AGENT NODE] Bắt đầu vòng lặp suy nghĩ thứ {iteration} ---")
        if iteration > self._max_iterations:
            print(f"[LOG: AGENT NODE] Đã đạt giới hạn vòng lặp ({self._max_iterations}).")
            steps.append({"type": "final_answer", "content":
                          "Agent reached its maximum tool-call limit."})
            return {"iteration": iteration, "steps": steps, "done": True}

        print("[LOG: AGENT NODE] Đang chờ LLM xử lý...")
        answer = self._complete(state["messages"], state["temperature"]).strip()
        print(f"[LOG: AGENT NODE] Output thô từ LLM (Biến đổi task user thành JSON):\n{answer}\n" + "-"*40)

        if not answer:
            print("[LOG: AGENT NODE] Lỗi: LLM trả về rỗng.")
            steps.append({"type": "error", "content": "The model returned an empty response."})
            return {"iteration": iteration, "steps": steps, "done": True}

        tool_name, tool_params = self._parse(answer)
        if tool_name is None:
            print(f"[LOG: AGENT NODE] Không có tool call. Đây là OUTPUT CUỐI CÙNG:\n{answer}")
            steps.append({"type": "final_answer", "content": answer})
            return {"iteration": iteration, "steps": steps, "done": True}

        params = tool_params or {}
        print(f"[LOG: AGENT NODE] Đã parse thành công Tool call: Tool='{tool_name}', Params={params}")
        steps.append({"type": "tool_call", "tool": tool_name,
                      "params": params, "iteration": iteration})
        messages = list(state["messages"])
        messages.append({"role": "assistant", "content": answer})

        if self._needs_approval(tool_name):
            return {
                "iteration": iteration,
                "steps": steps,
                "messages": messages,
                "pending_tool": {"tool": tool_name, "params": params},
                "done": True,
            }
        return {"iteration": iteration, "steps": steps, "messages": messages}

    @staticmethod
    def _after_reason(state: AgentState) -> str:
        if state["done"] or state["pending_tool"] is not None:
            print("--- [LOG: ROUTER] Chuyển hướng: Kết thúc (hoặc chờ phê duyệt tool) ---")
            return "end"
        print("--- [LOG: ROUTER] Chuyển hướng: Sang Tool Node ---")
        return "tool"

    def _tool(self, state: AgentState) -> dict[str, Any]:
        last_step = state["steps"][-1]
        tool_name = last_step["tool"]
        params = last_step["params"]
        
        print(f"\n--- [LOG: TOOL NODE] Bắt đầu thực thi Tool: {tool_name} ---")
        try:
            result = self._execute(tool_name, params)
            print(f"[LOG: TOOL NODE] Kết quả thực thi thành công: {result}")
        except Exception as error:  # noqa: BLE001
            # The server renders tool errors to the model.
            result = {"error": f"Tool exception: {error}"}
            print(f"[LOG: TOOL NODE] Lỗi thực thi tool: {result}")

        steps = list(state["steps"])
        steps.append({"type": "tool_result", "tool": tool_name,
                      "result": result, "iteration": state["iteration"]})
        result_text = json.dumps(result, ensure_ascii=False, indent=2)
        if len(result_text) > 8000:
            result_text = result_text[:8000] + "\n... [truncated]"
        messages = list(state["messages"])
        messages.append({
            "role": "user",
            "content": f"Tool `{tool_name}` returned:\n```json\n{result_text}\n```\n\nContinue your analysis or call another tool if needed.",
        })
        return {"steps": steps, "messages": messages}
