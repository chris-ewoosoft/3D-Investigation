# ruff: noqa: I001
import re
from collections.abc import Callable

from .config import *
from .config import _safe_relpath
from . import llm_module as llm_runtime
from .action_manifest import (
    validate_action_params,
)
from .tool_contract import (
    validate_tool_call,
)
from ai_assistant.observability import langsmith_trace, record_approval, record_schema_error, record_tool, span
from .inference import backend_mode, openai_compatible_completion, strip_think_tags
from ai_assistant.bootstrap.runtime import (
    execute_approved_tool as execute_approved_platform_tool,
)
from .coding_agent import CodingTaskContext, instruction as coding_instruction, is_coding_task
from .approval_manager import PendingActionStore
from .task_coordinator import coordinator as task_coordinator
from .multi_agent import (
    Specialist,
    audit as audit_agent,
    authorise as authorise_delegation,
    delegate,
    reflect_result,
    specialist_instruction,
    verify_result,
)

try:
    from .a2a_protocol import A2ARouter
except ImportError:  # pragma: no cover - retained for minimal deployments
    A2ARouter = None  # type: ignore[assignment,misc]

try:
    from LangGraphAgent import LocalAgentGraph
    LANGGRAPH_AVAILABLE = True
    LANGGRAPH_IMPORT_ERROR = ""
except ImportError as error:
    LocalAgentGraph = None
    LANGGRAPH_AVAILABLE = False
    LANGGRAPH_IMPORT_ERROR = str(error)

# ── Tool Definitions & Executors (Shim) ──────────────────────────────────────────

import threading
import time
import hashlib
from ai_assistant.tools.factory import create_tool_registry

TOOL_REGISTRY = create_tool_registry()

# Re-export variables used by the rest of the agent module
_TOOL_DEFINITIONS = {spec.name: spec.json_schema for spec in TOOL_REGISTRY.get_all()}
_TOOL_PARAM_MODELS = TOOL_REGISTRY.models
_LLAMA_CPP_TOOLS = TOOL_REGISTRY.get_openai_tools()
_TOOL_GRAMMAR_SCHEMA = TOOL_REGISTRY.grammar
_TOOLS_REQUIRING_APPROVAL = {spec.name for spec in TOOL_REGISTRY.get_all() if spec.requires_approval}
_AGENT_MAX_ITERATIONS = 12
AGENT_TOOLS = [spec.json_schema for spec in TOOL_REGISTRY.get_all()]

def platform_executors():
    executors = {}
    for spec in TOOL_REGISTRY.get_all():
        if spec.handler:
            executors[spec.name] = spec.handler
    return executors

_AGENT_BLOCKED_DIRS = {".git", "build", "__pycache__", ".vs", "node_modules"}
_AGENT_BLOCKED_EXTS = {".exe", ".dll", ".so", ".bin", ".dat", ".pkl", ".gguf", ".onnx", ".pt"}


# ── Pending actions storage (in-memory, per session) ──────────────────────────
_pending_lock = threading.Lock()
_PENDING_ACTIONS_FILE = os.path.join(APP_DATA_DIR, "AIAssistant", "pending_agent_actions.json")
_pending_actions = PendingActionStore(_PENDING_ACTIONS_FILE)

def _save_pending_actions() -> None:
    _pending_actions.save()

def _load_pending_actions() -> None:
    _pending_actions.load()
    if _pending_actions.cleanup(time.time() - 600):
        _save_pending_actions()

def _generate_action_id() -> str:
    return hashlib.sha256(f"{time.time()}-{threading.current_thread().ident}".encode()).hexdigest()[:12]

# Helpers required by agent UI action logic
def _canonical_desktop_action(params: dict) -> dict | None:
    from ai_assistant.tools.action_manifest import canonicalise_action_params
    return canonicalise_action_params(params)

def _looks_like_ui_action(text: str) -> bool:
    return False

def _match_desktop_action(task: str) -> dict | None:
    return None

def _match_desktop_action_sequence(task: str) -> list[dict] | None:
    return None

# ── Agent System Prompt (shim → ai_assistant.agents.prompts) ────────────────

from ai_assistant.agents.prompts import build_agent_system_prompt as _build_agent_system_prompt_new
from ai_assistant.agents.models import (
    AgentExecuteRequest,
    AgentApproveRequest,
    AgentUiActionResultRequest,
    AgentCancelRequest,
)

def _build_agent_system_prompt(language: str = "vi") -> str:
    """Shim: delegates to the new agents.prompts module."""
    return _build_agent_system_prompt_new(TOOL_REGISTRY, language)



def _extract_tool_call_xml(content: str) -> str | None:
    """Extract tool call from ``<tool_call>...</tool_call>`` XML envelope.

    Qwen text models using their native GGUF chat template emit tool calls
    as XML-wrapped JSON in the *content* field instead of populating the
    structured ``tool_calls`` response field.  This helper converts the XML
    envelope into the canonical ``{"kind": "tool", ...}`` JSON string that
    the rest of the agent pipeline expects.
    """
    match = re.search(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", content, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    # Qwen format: {"name": "tool_name", "arguments": {...}}
    # Fallback format: {"tool": "tool_name", "params": {...}}
    tool_name = data.get("name", "") or data.get("tool", "")
    arguments = data.get("arguments")
    if arguments is None:
        arguments = data.get("params", {})
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            return None
    if not tool_name or not isinstance(arguments, dict):
        return None
    envelope = {"kind": "tool", "tool": tool_name, "params": arguments}
    logger.info("Extracted tool call from <tool_call> XML envelope: %s",
                json.dumps(envelope, ensure_ascii=False))
    return json.dumps(envelope, ensure_ascii=False)


def _parse_tool_call(response_text: str) -> tuple:
    """Decode the JSON envelope emitted by llama.cpp constrained decoding.

    This intentionally no longer searches prose with regex.  The fallback
    accepts only a whole JSON object, so invalid/partial model output cannot
    accidentally execute a tool.
    """
    _TOOL_NAME_ALIASES = {
        "app_action_reconstruction": "application_action",
        "app_action_general":        "application_action",
        "app_action_ai":             "application_action",
        "app_action_viewer":         "application_action",
        "application_actions":       "application_action",
        "app_action":                "application_action",
        "desktop_action":            "application_action",
        "ui_action":                 "application_action",
    }

    def _normalise_tool_name(name: str) -> str:
        return _TOOL_NAME_ALIASES.get(name, name)

    try:
        data = json.loads(response_text.strip())
    except (json.JSONDecodeError, TypeError):
        return None, None
    if not isinstance(data, dict):
        return None, None
    if data.get("kind") == "step_answer":
        return "_step_answer", {"content": str(data.get("content", ""))}
    if data.get("kind") != "tool":
        return None, None
    tool_name = _normalise_tool_name(str(data.get("tool", "")))
    params = data.get("params")
    if not isinstance(params, dict):
        return None, None
    validated, error = validate_tool_call(tool_name, params, _TOOL_PARAM_MODELS)
    if error:
        logger.warning("Rejected invalid constrained tool call %s: %s", tool_name, error)
        record_schema_error(tool_name)
        return "_validation_error", {"tool": tool_name, "error": error}
    return tool_name, validated


_PRECOMPILED_TOOL_GRAMMAR = None

def _get_tool_grammar():
    global _PRECOMPILED_TOOL_GRAMMAR
    if _PRECOMPILED_TOOL_GRAMMAR is None:
        try:
            from llama_cpp import LlamaGrammar
            _PRECOMPILED_TOOL_GRAMMAR = LlamaGrammar.from_json_schema(_TOOL_GRAMMAR_SCHEMA)
        except ImportError:
            _PRECOMPILED_TOOL_GRAMMAR = None
    return _PRECOMPILED_TOOL_GRAMMAR


def _constrained_agent_completion(messages: list[dict], max_tokens: int, temperature: float) -> str:
    """Generate exactly one final/tool envelope with llama.cpp grammar.

    Grammar is the default because it works with local models that do not
    implement a native function-calling chat template. Set
    ``AGENT_NATIVE_TOOL_CALLS=1`` to use llama-cpp-python's OpenAI ``tools``
    interface instead; both paths pass through the same Pydantic validation.
    """
    try:
        # logger.info("Constrained LLM messages: %s", json.dumps(messages, ensure_ascii=False, default=str))
        if backend_mode() != "llama_cpp":
            response = openai_compatible_completion(
                messages, max_tokens=max_tokens, temperature=temperature,
                tools=_LLAMA_CPP_TOOLS, tool_choice="auto",
                response_format={"type": "json_object"},
            )
            
            usage = response.get("usage", {})
            in_tok = usage.get("prompt_tokens", 0)
            out_tok = usage.get("completion_tokens", 0)
            if in_tok or out_tok:
                from ai_assistant.observability import record_token_usage
                record_token_usage(in_tok, out_tok)

            message = response.get("choices", [{}])[0].get("message", {})
            if message.get("tool_calls"):
                call = message["tool_calls"][0]["function"]
                content = json.dumps({"kind": "tool", "tool": call["name"],
                                      "params": json.loads(call.get("arguments", "{}"))}, ensure_ascii=False)
                logger.info("Constrained LLM response: %s", content)
                return content
            content = message.get("content", "")
            # Remote deployments are expected to return the same envelope.
            logger.info("Constrained LLM response: %s", content)
            return content
        kwargs = {
            "messages": messages, "max_tokens": max_tokens,
            "temperature": temperature, "repeat_penalty": 1.1, "stream": False,
        }
        use_native = os.environ.get("AGENT_NATIVE_TOOL_CALLS", "0") == "1"
        if use_native:
            kwargs.update({"tools": _LLAMA_CPP_TOOLS, "tool_choice": "auto"})
        else:
            grammar = _get_tool_grammar()
            if grammar is not None:
                kwargs.update({"grammar": grammar})
        with llm_runtime.llm_lock:
            response = llm_runtime.llm.create_chat_completion(**kwargs)
    except Exception as error:  # noqa: BLE001
        raise RuntimeError(f"Constrained tool decoding failed: {error}") from error

    usage = response.get("usage", {})
    in_tok = usage.get("prompt_tokens", 0)
    out_tok = usage.get("completion_tokens", 0)
    if in_tok or out_tok:
        from ai_assistant.observability import record_token_usage
        record_token_usage(in_tok, out_tok)

    message = response.get("choices", [{}])[0].get("message", {})
    if message.get("tool_calls"):
        call = message["tool_calls"][0]["function"]
        content = json.dumps({"kind": "tool", "tool": call["name"],
                              "params": json.loads(call.get("arguments", "{}"))}, ensure_ascii=False)
        logger.info("Constrained LLM response: %s", content)
        return content
    content = message.get("content", "")
    if not isinstance(content, str):
        raise RuntimeError("Constrained decoder returned no text content")
    content = strip_think_tags(content)
    logger.info("Constrained LLM response: %s", content)
    # Detect <tool_call> XML envelope emitted by Qwen text models
    if "<tool_call>" in content:
        xml_result = _extract_tool_call_xml(content)
        if xml_result:
            return xml_result
    try:
        # Xử lý trường hợp model sinh ra thêm văn bản rác sau chuỗi JSON
        content_stripped = content.strip()
        idx = content_stripped.find('{')
        if idx != -1:
            json_str = content_stripped[idx:]
            envelope, _ = json.JSONDecoder().raw_decode(json_str)
        else:
            envelope = json.loads(content)
    except json.JSONDecodeError as error:
        logger.warning("Constrained decoder returned non-JSON text, treating as final response: %s", error)
        return content
        
    if not isinstance(envelope, dict):
        logger.warning("Constrained decoder returned a non-object JSON value, treating as raw text.")
        return content
    if envelope.get("kind") == "final" and isinstance(envelope.get("content"), str):
        return envelope["content"]
    if envelope.get("kind") == "step_answer" and isinstance(envelope.get("content"), str):
        # Preserve the envelope so LangGraph can mark the active plan step as
        # complete.  Returning only its text makes it indistinguishable from a
        # final answer and causes the reasoning loop to repeat the same step.
        return json.dumps(envelope, ensure_ascii=False)
    if envelope.get("kind") == "tool":
        return json.dumps(envelope, ensure_ascii=False)
        
    logger.warning("Constrained decoder returned an unsupported envelope, treating as raw text.")
    return content


_PLANNER_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "requires_plan": {"type": "boolean"},
        "goal": {"type": "string"},
        "affected_areas": {"type": "array", "items": {"type": "string"}},
        "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
        "verification_commands": {"type": "array", "items": {"type": "string"}},
        "steps": {"type": "array", "items": {"type": "string"}},
        # Kept for persisted/older planner clients; new prompts emit steps.
        "plan": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["requires_plan", "steps"],
    "additionalProperties": False,
}
_CRITIC_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "passed": {"type": "boolean"},
        "decision": {"type": "string", "enum": ["continue", "revise"]},
        "reason": {"type": "string"},
    },
    "required": ["passed", "decision", "reason"],
    "additionalProperties": False,
}


def _structured_agent_completion(messages: list[dict], max_tokens: int,
                                 temperature: float, schema: dict) -> str:
    """Generate internal planner/critic JSON without the ReAct tool schema."""
    try:
        if backend_mode() != "llama_cpp":
            response = openai_compatible_completion(
                messages, max_tokens=max_tokens, temperature=temperature,
                response_format={"type": "json_object"},
            )
        else:
            from llama_cpp import LlamaGrammar  # imported lazily for testability
            with llm_runtime.llm_lock:
                response = llm_runtime.llm.create_chat_completion(
                    messages=messages, max_tokens=max_tokens, temperature=temperature,
                    repeat_penalty=1.1, stream=False,
                    # llama-cpp-python expects a serialized JSON Schema here;
                    # passing the Python dict raises ``JSON object must be str``.
                    grammar=LlamaGrammar.from_json_schema(json.dumps(schema)),
                )
    except Exception as error:  # noqa: BLE001
        raise RuntimeError(f"Structured JSON decoding failed: {error}") from error

    content = response.get("choices", [{}])[0].get("message", {}).get("content", "")
    if not isinstance(content, str):
        raise RuntimeError("Structured decoder returned no text content")
    content = strip_think_tags(content)
    logger.info("Structured LLM response: %s", content)
    return content


def _run_langgraph_agent(system_prompt: str, task: str, session_id: str,
                         temperature: float, language: str, request_started: float,
                         initial_messages: list[dict[str, str]] | None = None,
                          initial_steps: list[dict] | None = None,
                          initial_iteration: int = 0,
                          resume_with_reflection: bool = False,
                          event_sink: Callable[[dict], None] | None = None,
                          prior_step_count: int = 0,
                          approval_granted: bool = False,
                          approval_scope: str = "",
                          supervisor_route: Specialist | None = None) -> dict:
    """Run the tool loop through LangGraph while preserving the Qt API response."""
    if not LANGGRAPH_AVAILABLE or LocalAgentGraph is None:
        raise HTTPException(
            status_code=503,
            detail="LangGraph is required for Agent mode. Run: pip install -r AIAssistant/requirements.txt",
        )
    if supervisor_route is None:
        supervisor_route = Specialist.SUPERVISOR

    def complete(messages: list[dict[str, str]], current_temperature: float) -> str:
        total_chars = sum(len(message.get("content", "")) for message in messages)
        estimated_tokens = int(total_chars / CHARS_PER_TOKEN)
        if estimated_tokens >= LLM_N_CTX - 512:
            return "Context quá dài, dừng Agent."
        max_tokens = min(2048, max(512, LLM_N_CTX - estimated_tokens - 400))

        def call(msgs: list[dict[str, str]]) -> str:
            return _constrained_agent_completion(msgs, max_tokens, current_temperature)

        print(f"[AGENT TRACE] ── LangGraph: Gọi LLM ({len(messages)} msgs, ~{estimated_tokens} tokens)", flush=True)
        logger.info("LangGraph gọi Model (messages: %d, estimated_tokens: %d)", len(messages), estimated_tokens)
        answer = call(messages)
        print(f"[AGENT TRACE] ── LangGraph: LLM output ({len(answer)} chars): {answer[:120].replace(chr(10), ' ')}", flush=True)
        logger.info("LangGraph nhận phản hồi từ Model (length: %d chars)", len(answer))

        # [FIX-13] Self-correction NGAY TRONG vòng lặp LangGraph: ở lượt suy
        # luận đầu tiên (messages chỉ gồm system+user, chưa có tool nào chạy),
        # nếu model trả lời bằng văn bản thường (không phát ```tool_call```)
        # trong khi câu hỏi của người dùng mang dáng dấp một lệnh điều khiển
        # UI (rule #11 trong system prompt), cho model MỘT cơ hội tự sửa bằng
        # một system reminder nhấn mạnh rule #11, trước khi chấp nhận đó là
        return answer

    def execute(tool_name: str, params: dict) -> dict:
        delegation = delegate(task, session_id, tool_name, params, prefer_code=is_coding_task(task))
        allowed, reason = authorise_delegation(delegation, tool_name in _TOOLS_REQUIRING_APPROVAL)
        if not allowed:
            audit_agent("tool_denied", delegation, reason=reason)
            return {"error": reason or "Tool call denied by supervisor policy."}
        if tool_name == "application_action":
            canonical_params, error = validate_action_params(params)
            if error:
                return {"error": error}
            canonical_params["request_id"] = _generate_action_id()
            params.clear()
            params.update(canonical_params)
        if tool_name == "_validation_error":
            return {"error": f"Lỗi xác thực tham số tool '{params.get('tool')}': {params.get('error')}"}
        spec = TOOL_REGISTRY.get(tool_name)
        if spec is None or spec.handler is None:
            return {"error": f"Tool không tồn tại hoặc không có handler: {tool_name}"}
        executor = spec.handler
        tool_started = time.monotonic()

        def execute_local(_: str, __: str, ___: dict | None) -> dict:
            with span("agent.tool", tool=tool_name, session_id=session_id):
                return executor(params)

        if delegation.remote_endpoint and A2ARouter is not None:
            remote_payload = {
                "tool": tool_name,
                "parameters": params,
                "session_id": session_id,
                "idempotency_key": delegation.idempotency_key,
            }
            with span("agent.a2a_delegate", tool=tool_name,
                      specialist=delegation.specialist.value,
                      remote_endpoint=delegation.remote_endpoint):
                result = A2ARouter().route(
                    delegation.specialist.value, task, remote_payload,
                )
            audit_agent("tool_transport", delegation, source=result.get("source", "local"))
        else:
            result = execute_local(delegation.specialist.value, task, None)
        audit_agent("tool_completed", delegation, success="error" not in result)
        record_tool(tool_name, "error" not in result, time.monotonic() - tool_started)
        return result

    def select_specialist(tool_name: str, params: dict) -> dict:
        if tool_name == "_validation_error":
            return {}
        delegation = delegate(task, session_id, tool_name, params, prefer_code=is_coding_task(task))
        audit_agent("tool_delegated", delegation)
        return {
            "specialist": str(delegation.specialist),
            "idempotency_key": delegation.idempotency_key,
            "instruction": specialist_instruction(delegation),
            "remote_endpoint": delegation.remote_endpoint,
        }

    def verify_tool_result(tool_name: str, params: dict, result: dict) -> dict:
        if tool_name == "_validation_error":
            return {"passed": False, "reason": result.get("error", "Validation error")}
        delegation = delegate(task, session_id, tool_name, params, prefer_code=is_coding_task(task))
        verification = verify_result(delegation, result)
        audit_agent("tool_verified", delegation, **verification)
        return verification

    def deterministic_reflection(tool_name: str, params: dict, result: dict,
                                 verification: dict) -> dict:
        delegation = delegate(task, session_id, tool_name, params, prefer_code=is_coding_task(task))
        reflection = reflect_result(delegation, result, verification)
        audit_agent("tool_reflected", delegation, **reflection)
        return reflection

    logger.info("Khởi động LangGraph vòng lặp thực thi tool (session: %s)", session_id)
    print(f"[AGENT TRACE] ▶ LangGraph session={session_id} task={task[:80].replace(chr(10), ' ')}", flush=True)

    # LangSmith: wrap entire agent session as a top-level traced run.
    _ls_ctx_mgr = langsmith_trace(
        "agent.session",
        run_type="chain",
        inputs={"task": task[:200], "session_id": session_id,
                "supervisor_route": supervisor_route.value if supervisor_route else "none"},
        metadata={"session_id": session_id, "temperature": temperature,
                  "language": language},
    )
    _ls_ctx = _ls_ctx_mgr.__enter__()
    graph = LocalAgentGraph(
        complete=complete,      # Gọi model để sinh ra câu trả lời
        parse=_parse_tool_call, # Parse tool_call ra khỏi câu trả lời
        execute=execute,        # Thực thi tool
        needs_approval=lambda tool_name: tool_name in _TOOLS_REQUIRING_APPROVAL, # Kiểm tra xem có cần approval không
        max_iterations=_AGENT_MAX_ITERATIONS, # Số lần lặp tối đa
        emit=event_sink,
        select_specialist=select_specialist,
        verify_result=verify_tool_result,
        reflect_result=deterministic_reflection,
        plan_complete=lambda messages, temp: _structured_agent_completion(
            messages, 512, temp, _PLANNER_JSON_SCHEMA),
        reflect_complete=lambda messages, temp: _structured_agent_completion(
            messages, 512, temp, _CRITIC_JSON_SCHEMA),
        plan_reflect_complete=lambda messages, temp: _structured_agent_completion(
            messages, 512, temp, _CRITIC_JSON_SCHEMA),
        cancel_checker=lambda: task_coordinator.is_cancelled(session_id),
    )
    messages = initial_messages or [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": task},
    ]
    # An explicit manifest workflow is optional context only.  A single action
    # match is not promoted to a sequence; every plan step remains an LLM
    # tool-calling decision and is reviewed by Reflect.
    matched_ui_actions = []
    try:
        state = graph.run(messages, session_id, temperature, initial_steps, initial_iteration,
                          resume_with_reflection=resume_with_reflection,
                          required_ui_actions=matched_ui_actions,
                          supervisor_route=supervisor_route.value,
                          enforce_plan_completion=True,
                          approval_granted=approval_granted or bool((initial_steps or []) and
                                                any(step.get("type") == "approval_granted"
                                                    for step in (initial_steps or []))),
                          approval_scope=approval_scope)
    except BaseException as error:
        _ls_ctx["outputs"] = {"status": "error", "error": str(error)}
        _ls_ctx_mgr.__exit__(type(error), error, error.__traceback__)
        raise
    pending_state = state.get("pending_tool") or {}
    pending_status = ("pending_ui_action" if pending_state.get("ui_ack")
                      else "pending_approval" if pending_state else "completed")
    if state.get("cancelled"):
        pending_status = "cancelled"
    if pending_state:
        task_coordinator.update(session_id, status="waiting_approval" if not pending_state.get("ui_ack")
                                else "waiting_ui_ack", iterations=state.get("iteration", 0))
    else:
        task_coordinator.finish(session_id, success=not state.get("cancelled"),
                                iterations=state.get("iteration", 0))
    total_lg_ms = round((time.monotonic() - request_started) * 1000)
    print(f"[AGENT TRACE] ✓ Done (LangGraph) | iter={state.get('iteration', 0)} status={pending_status} | {total_lg_ms}ms", flush=True)
    logger.info("LangGraph hoàn thành vòng lặp execution (iteration: %d, status: %s)", state.get("iteration", 0), pending_status)

    # Close LangSmith session trace with final outputs.
    _ls_ctx["outputs"] = {
        "status": pending_status,
        "iterations": state.get("iteration", 0),
        "duration_ms": total_lg_ms,
        "step_count": len(state.get("steps", [])),
    }
    _ls_ctx_mgr.__exit__(None, None, None)
    steps = state["steps"]
    pending = state.get("pending_tool")
    if pending:
        action_id = _generate_action_id()
        is_ui_ack = bool(pending.get("ui_ack"))
        request_id = pending["params"].get("request_id", action_id)
        with _pending_lock:
            _pending_actions[request_id if is_ui_ack else action_id] = {
                "tool": pending["tool"],
                "params": pending["params"],
                "session_id": session_id,
                "task": task,
                "messages": state["messages"],
                "steps": steps,
                "iteration": state["iteration"],
                "temperature": temperature,
                "language": language,
                "created_at": time.time(),
                "ui_ack": is_ui_ack,
                "approval_scope": pending.get("approval_scope", ""),
                "approval_preview": pending.get("approval_preview"),
            }
            _save_pending_actions()
        if is_ui_ack:
            return {
                "status": "pending_ui_action", "session_id": session_id,
                "steps": steps, "request_id": request_id,
                "ui_action": {"request_id": request_id, "action": pending["params"]["action"],
                              "params": pending["params"]},
                "total_ms": round((time.monotonic() - request_started) * 1000),
            }
        steps.append({
            "type": "pending_approval",
            "action_id": action_id,
            "tool": pending["tool"],
            "params": pending["params"],
            "description": pending["params"].get("description", f"Thực thi {pending['tool']}"),
            "approval_scope": pending.get("approval_scope", ""),
            "preview": pending.get("approval_preview"),
        })
        return {
            "status": "pending_approval", "session_id": session_id,
            "steps": steps, "action_id": action_id,
            "prior_step_count": prior_step_count,
            "total_ms": round((time.monotonic() - request_started) * 1000),
        }

    if not any(step["type"] == "final_answer" for step in steps):
        steps.append({"type": "final_answer", "content": "Agent đã kết thúc mà chưa có kết luận."})
    return {
        "status": "completed", "session_id": session_id, "steps": steps,
        "prior_step_count": prior_step_count,
        "iterations": state["iteration"],
        "total_ms": round((time.monotonic() - request_started) * 1000),
    }



# ── Agent Endpoints ───────────────────────────────────────────────────────────

_load_pending_actions()


import sys
from ai_assistant.adapters.http.agent_routes import (  # noqa: E402
    _agent_response,
    _stream_langgraph_execution,
    build_agent_router,
)


def agent_execute(request: AgentExecuteRequest, http_req: Request):
    """
    Execute an agentic task with tool-calling loop.
    Returns a list of steps (tool_call, tool_result, thinking, final_answer, pending_approval).
    """
    _cleanup_pending_actions()
    if llm_runtime.llm is None:
        raise HTTPException(status_code=503, detail="LLM chưa khởi tạo")

    req_start = time.monotonic()
    task = request.task
    session_id = request.session_id or "agent_default"
    task_coordinator.start(session_id, task=request.task)
    
    retry_idx = request.retry_message_index

    print(f"[AGENT TRACE] ▶ Session={session_id} | LangGraph=ON | Task={task[:80].replace(chr(10), ' ')}", flush=True)
    logger.info("[MODE: AGENT] Task from %s: %s…", http_req.client.host, task[:80].replace("\n", " "))

    system_prompt = _build_agent_system_prompt(request.language)
    if is_coding_task(task):
        system_prompt += "\n\n" + coding_instruction(CodingTaskContext(
            task=task, language=request.language, project_root=_safe_relpath(PROJECT_DIR, PROJECT_DIR),
        ))

    # Agent mode obtains project evidence through the explicit ``rag_search``
    # tool. Do not eagerly append the same retrieval to the system prompt: it
    # duplicates the subsequent tool result and can exhaust the 8k context
    # window before the agent has a chance to answer from the retrieved data.

    history_messages: list[dict[str, str]] = []
    for entry in request.history:
        role = entry.get("role")
        content_msg = entry.get("content")
        if role in {"user", "assistant"} and isinstance(content_msg, str) and content_msg.strip():
            history_messages.append({"role": role, "content": content_msg[:32000]})

    task_with_attachments = task
    if request.attachments:
        names = [os.path.basename(path) for path in request.attachments]
        task_with_attachments += "\n\n[Attached files: " + ", ".join(names) + "]"

    if "text/event-stream" in http_req.headers.get("accept", ""):
        return _stream_langgraph_execution(
            lambda sink: _run_langgraph_agent(system_prompt, task_with_attachments, session_id,
                                               request.temperature, request.language, req_start,
                                               initial_messages=[{"role": "system", "content": system_prompt},
                                                                 *history_messages,
                                                                 {"role": "user", "content": task_with_attachments}],
                                               event_sink=sink, supervisor_route=Specialist.SUPERVISOR))
    result = _run_langgraph_agent(
        system_prompt, task_with_attachments, session_id, request.temperature, request.language, req_start,
        initial_messages=[{"role": "system", "content": system_prompt}, *history_messages,
                          {"role": "user", "content": task_with_attachments}], supervisor_route=Specialist.SUPERVISOR)
    
    if retry_idx is not None:
        result["retry_message_index"] = retry_idx
    return _agent_response(result, http_req)

def agent_cancel(request: AgentCancelRequest):
    """Request cooperative cancellation for a running session/request."""
    cancelled = task_coordinator.cancel(request.session_id, request.request_id)
    if cancelled is None:
        raise HTTPException(status_code=404, detail="Unknown or already finished agent task")
    return {"status": "cancelled", **cancelled}


def agent_ui_action_result(request: AgentUiActionResultRequest):
    """Close the desktop-action loop after the Qt slot has run."""
    _cleanup_pending_actions()
    with _pending_lock:
        action = _pending_actions.pop(request.request_id, None)
        _save_pending_actions()
    if action is None or not action.get("ui_ack"):
        raise HTTPException(status_code=404, detail="Unknown or expired UI action request")

    params = action["params"]
    result = {"success": request.success, "action": params["action"], **request.result}
    steps = []
    steps.append({"type": "tool_result", "tool": "application_action", "request_id": request.request_id,
                  "result": result, "iteration": action.get("iteration", 0)})
    # The initial dispatch only proves that Qt received the request.  Verify the
    # actual ACK separately so reflect evaluates the desktop outcome, including
    # a failure reported by the client.
    delegation = delegate(action["task"], action["session_id"], "application_action", params)
    verification = verify_result(delegation, result)
    audit_agent("tool_verified", delegation, **verification)
    steps.append({"type": "verification", "tool": "application_action", "result": verification,
                  "iteration": action.get("iteration", 0)})

    # Transitional Qt clients may send a server-generated sequence of already
    # canonical actions. Keep it as a compatibility adapter only: selection is
    # still made by the planner, and the next action receives its own durable
    # acknowledgement request rather than being executed implicitly.
    next_actions = action.get("next_actions") or []
    if request.success and isinstance(next_actions, list) and next_actions:
        queued = next_actions[0]
        if not isinstance(queued, dict):
            raise HTTPException(status_code=422, detail="Invalid queued UI action")
        next_params, error = validate_action_params(queued)
        if error:
            raise HTTPException(status_code=422, detail=error)
        next_request_id = _generate_action_id()
        next_params["request_id"] = next_request_id
        all_steps = action.get("steps", []) + steps + [{
            "type": "tool_call", "tool": "application_action", "params": next_params,
            "iteration": action.get("iteration", 0) + 1,
        }]
        with _pending_lock:
            _pending_actions[next_request_id] = {
                "ui_ack": True, "params": next_params, "task": action["task"],
                "session_id": action["session_id"], "steps": all_steps,
                "messages": action.get("messages", []), "next_actions": next_actions[1:],
                "iteration": action.get("iteration", 0) + 1,
                "temperature": action.get("temperature", 0.3),
                "language": action.get("language", "vi"), "created_at": time.time(),
            }
            _save_pending_actions()
        return {
            "status": "pending_ui_action", "session_id": action["session_id"],
            "request_id": next_request_id, "prior_step_count": len(action.get("steps", [])),
            "steps": all_steps,
            "ui_action": {"request_id": next_request_id, "action": next_params["action"], "params": next_params},
        }



    # Nếu LLM (LangGraph) đang chạy, tiếp tục graph để thực hiện bước tiếp theo trong kế hoạch
    if action.get("messages") and USE_LANGGRAPH_AGENT and LANGGRAPH_AVAILABLE:
        messages = action["messages"]
        tool_call_text = json.dumps({"tool": "application_action", "params": params}, ensure_ascii=False)
        messages.append({"role": "assistant", "content": f"```tool_call\n{tool_call_text}\n```"})
        
        result_text = json.dumps(result, ensure_ascii=False, indent=2)
        if len(result_text) > 8000:
            result_text = result_text[:8000] + "\n... [truncated]"
        messages.append({
            "role": "user",
            "content": f"Tool `application_action` returned:\n```json\n{result_text}\n```\n\nPhân tích kết quả. NẾU kế hoạch của bạn CÒN bước tiếp theo, hãy bắt buộc GỌI TOOL cho bước đó ngay lập tức (KHÔNG HỎI LẠI NGƯỜI DÙNG). Nếu đã hoàn thành toàn bộ, đưa ra thông báo kết thúc."
        })
        
        system_prompt = messages[0]["content"] if messages and messages[0].get("role") == "system" else _build_agent_system_prompt(action.get("language", "vi"))
        return _run_langgraph_agent(
            system_prompt=system_prompt,
            task=action["task"],
            session_id=action["session_id"],
            temperature=action["temperature"],
            language=action.get("language", "vi"),
            request_started=time.monotonic(),
            initial_messages=messages,
            initial_steps=action.get("steps", []) + steps,
            initial_iteration=action.get("iteration", 0),
            resume_with_reflection=True,
            prior_step_count=len(action.get("steps", [])),
        )

    content = (f"Đã thực thi {params['action']}." if request.success
               else f"Không thể thực thi {params['action']}: {result.get('error', 'unknown error')}")
    steps.append({"type": "final_answer", "content": content})
    all_steps = action.get("steps", []) + steps
    task_coordinator.finish(action["session_id"], success=request.success)
    retry_idx_stored = action.get("retry_message_index")
    return {
        "status": "completed" if request.success else "failed",
        "session_id": action["session_id"],
        "request_id": request.request_id,
        "prior_step_count": len(action.get("steps", [])),
        "steps": all_steps,
        **({"retry_message_index": retry_idx_stored} if retry_idx_stored is not None else {}),
    }


def agent_approve(request: AgentApproveRequest, http_req: Request):
    """
    Approve or reject a pending agent action (write_file, run_command).
    If approved, executes the action and resumes the agent loop.
    """
    _cleanup_pending_actions()
    if llm_runtime.llm is None:
        raise HTTPException(status_code=503, detail="LLM chưa khởi tạo")

    action_id = request.action_id
    with _pending_lock:
        action = _pending_actions.pop(action_id, None)
        _save_pending_actions()

    if action is None:
        record_approval("missing")
        raise HTTPException(status_code=404, detail=f"Action not found: {action_id}")

    if task_coordinator.is_cancelled(action.get("session_id", "")):
        task_coordinator.finish(action.get("session_id", ""), success=False)
        return {"status": "cancelled", "action_id": action_id,
                "steps": [*action.get("steps", []), {
                    "type": "cancelled", "content": "Task cancelled before approval."}]}

    if request.session_id and request.session_id != action.get("session_id"):
        with _pending_lock:
            _pending_actions[action_id] = action
            _save_pending_actions()
        record_approval("unauthorized")
        raise HTTPException(status_code=403, detail="Action does not belong to this session")
    if not request.approved:
        record_approval("rejected")
        # User rejected
        return {
            "status": "rejected",
            "action_id": action_id,
            "approval_scope": action.get("approval_scope", ""),
            "approval_preview": action.get("approval_preview"),
            "prior_step_count": len(action["steps"]),
            "steps": action["steps"] + [{
                "type": "tool_result",
                "tool": action["tool"],
                "action_id": action_id,
                "result": {"rejected": True, "message": "Người dùng từ chối thực thi action này."},
                "iteration": action["iteration"],
            }],
        }

    # Execute the approved action
    record_approval("approved")
    tool_name = action["tool"]
    tool_params = action["params"]

    approved_tool_started = time.monotonic()
    tool_result = execute_approved_platform_tool(tool_name, tool_params)
    record_tool(tool_name, "error" not in tool_result, time.monotonic() - approved_tool_started)

    prior_step_count = len(action["steps"])
    steps = action["steps"]
    steps.append({
        "type": "tool_result",
        "tool": tool_name,
        "action_id": action_id,
        "result": tool_result,
        "iteration": action["iteration"],
    })
    delegation = delegate(action["task"], action["session_id"], tool_name, tool_params)
    verification = verify_result(delegation, tool_result)
    audit_agent("tool_verified", delegation, **verification)
    steps.append({
        "type": "verification", "tool": tool_name, "result": verification,
        "iteration": action["iteration"],
    })

    # Resume agent loop with remaining context
    messages = action["messages"]
    # Add the tool call and result to messages
    tool_call_text = json.dumps({"tool": tool_name, "params": tool_params}, ensure_ascii=False)
    messages.append({"role": "assistant", "content": f"```tool_call\n{tool_call_text}\n```"})

    result_text = json.dumps(tool_result, ensure_ascii=False, indent=2)
    if len(result_text) > 8000:
        result_text = result_text[:8000] + "\n... [truncated]"
    messages.append({
        "role": "user",
        "content": f"Tool `{tool_name}` was approved and executed. Result:\n```json\n{result_text}\n```\n\nContinue with your analysis or provide final answer.",
    })

    if USE_LANGGRAPH_AGENT and LANGGRAPH_AVAILABLE:
        system_prompt = messages[0]["content"] if messages and messages[0].get("role") == "system" else _build_agent_system_prompt(action.get("language", "vi"))
        steps.append({"type": "approval_granted", "scope_id": action.get("approval_scope", ""),
                      "tool": tool_name, "action_id": action_id})
        return _run_langgraph_agent(
            system_prompt=system_prompt,
            task=action["task"],
            session_id=action["session_id"],
            temperature=action["temperature"],
            language=action.get("language", "vi"),
            request_started=time.monotonic(),
            initial_messages=messages,
            initial_steps=steps,
            initial_iteration=action["iteration"],
            resume_with_reflection=True,
            prior_step_count=prior_step_count,
            approval_granted=True,
            approval_scope=action.get("approval_scope", ""),
        )

    # Continue the agent loop
    iteration = action["iteration"]
    temperature = action["temperature"]
    req_start = time.monotonic()
    approval_already_granted = True

    while iteration < _AGENT_MAX_ITERATIONS:
        iteration += 1

        total_chars = sum(len(m.get("content", "")) for m in messages)
        estimated_tokens = int(total_chars / CHARS_PER_TOKEN)
        available_tokens = LLM_N_CTX - estimated_tokens - 400
        max_tokens = min(2048, max(512, available_tokens))

        if estimated_tokens >= LLM_N_CTX - 512:
            steps.append({"type": "error", "content": "Context quá dài."})
            break

        try:
            answer = _constrained_agent_completion(messages, max_tokens, temperature)
        except Exception as e:
            steps.append({"type": "error", "content": f"Lỗi LLM: {e}"})
            break

        answer = answer.strip()
        if not answer:
            break

        tool_name_next, tool_params_next = _parse_tool_call(answer)

        if tool_name_next == "application_action":
            canonical_params_next = _canonical_desktop_action(tool_params_next)
            if canonical_params_next is not None:
                tool_params_next = canonical_params_next

        if tool_name_next is None:
            steps.append({"type": "final_answer", "content": answer})
            break

        clean_answer = strip_think_tags(answer)
        think_text = clean_answer
        if "```tool_call" in clean_answer:
            think_text = clean_answer.split("```tool_call")[0].strip()
        elif "{" in clean_answer:
            think_text = clean_answer.split("{")[0].strip()
            
        if think_text:
            steps.append({"type": "thinking", "content": think_text, "iteration": iteration})

        steps.append({
            "type": "tool_call",
            "tool": tool_name_next,
            "params": tool_params_next,
            "iteration": iteration,
        })

        if tool_name_next in _TOOLS_REQUIRING_APPROVAL and not approval_already_granted:
            new_action_id = _generate_action_id()
            with _pending_lock:
                _pending_actions[new_action_id] = {
                    "tool": tool_name_next,
                    "params": tool_params_next,
                    "session_id": action["session_id"],
                    "task": action["task"],
                    "messages": messages.copy(),
                    "steps": steps.copy(),
                    "iteration": iteration,
                    "temperature": temperature,
                    "created_at": time.time(),
                    "approval_scope": action.get("approval_scope", ""),
                }
                _save_pending_actions()
            task_coordinator.update(action["session_id"], new_action_id, status="waiting_approval")

            steps.append({
                "type": "pending_approval",
                "action_id": new_action_id,
                "tool": tool_name_next,
                "params": tool_params_next,
                "description": tool_params_next.get("description", f"Thực thi {tool_name_next}"),
            })

            total_ms = (time.monotonic() - req_start) * 1000
            return {
                "status": "pending_approval",
                "session_id": action["session_id"],
                "prior_step_count": prior_step_count,
                "steps": steps,
                "action_id": new_action_id,
                "total_ms": round(total_ms),
            }

        if tool_name_next == "_validation_error":
            tool_result_next = {"error": f"Lỗi xác thực tham số tool '{tool_params_next.get('tool')}': {tool_params_next.get('error')}"}
        elif tool_name_next in TOOL_REGISTRY.names():
            try:
                tool_result_next = TOOL_REGISTRY.execute(tool_name_next, tool_params_next)
            except Exception as e:
                tool_result_next = {"error": f"Tool exception: {e}"}
        else:
            tool_result_next = {"error": f"Tool không tồn tại: {tool_name_next}"}

        steps.append({
            "type": "tool_result",
            "tool": tool_name_next,
            "result": tool_result_next,
            "iteration": iteration,
        })

        messages.append({"role": "assistant", "content": answer})
        result_text_next = json.dumps(tool_result_next, ensure_ascii=False, indent=2)
        if len(result_text_next) > 8000:
            result_text_next = result_text_next[:8000] + "\n... [truncated]"
        messages.append({
            "role": "user",
            "content": f"Tool `{tool_name_next}` returned:\n```json\n{result_text_next}\n```\n\nContinue.",
        })

    if not any(s["type"] == "final_answer" for s in steps):
        steps.append({
            "type": "final_answer",
            "content": "⚠️ Agent đã đạt giới hạn iterations.",
        })

    total_ms = (time.monotonic() - req_start) * 1000
    return {
        "status": "completed",
        "session_id": action["session_id"],
        "prior_step_count": prior_step_count,
        "steps": steps,
        "iterations": iteration,
        "total_ms": round(total_ms),
    }


agent_router = build_agent_router(sys.modules[__name__])


# Cleanup expired pending actions (older than 10 minutes)
def _cleanup_pending_actions():
    cutoff = time.time() - 600
    with _pending_lock:
        had_expired = _pending_actions.cleanup(cutoff)
        if had_expired:
            _save_pending_actions()
            logger.info("Cleaned up expired pending agent actions")


def reset_agent_state() -> None:
    """Forget all pending approvals and their persisted state."""
    with _pending_lock:
        _pending_actions.clear()
        try:
            if os.path.exists(_PENDING_ACTIONS_FILE):
                os.remove(_PENDING_ACTIONS_FILE)
        except OSError as error:
            logger.warning("Unable to remove pending action state: %s", error)


