# ADR 0022 — True-lazy composition root + full-strict mypy: đóng 10/10

Hai mục còn lại của review 9.7/10. Cả hai đều là "claim chưa được pin"
(ADR 0015): docstring nói import "only defines" nhưng vẫn kéo cả dependency
graph, và config giữ `strict` từng phần với comment "``--strict`` còn ~230
lỗi" đã lỗi thời.

- **Import không còn kéo framework.**
  `bootstrap/server.py` top-level chỉ còn stdlib + `ai_assistant.settings`
  (pure stdlib, chủ sở hữu môi trường — ADR 0001/0014). Mọi import nặng
  (FastAPI/routers, LangGraph, llama.cpp, MCP SDK, RAG, tools) dời vào trong
  `compose()` hoặc helper dùng nó (`_LLMModule`, chatbot getter, A2A/MCP
  registration, `bootstrap_runtime`); annotation dùng
  `from __future__ import annotations` + `TYPE_CHECKING` nên mypy strict vẫn
  thấy kiểu đầy đủ. `bootstrap/__init__.py` chuyển sang lazy PEP 562
  (`__getattr__` load `container`/`app_factory` theo tên): riêng việc
  `import ai_assistant.bootstrap.server` đã chạy `__init__` của package cha
  và trước đây chính nó đã kéo FastAPI qua `app_factory`.
- **Pin bằng test, không bằng lời.**
  `LazyCompositionTests` chạy subprocess interpreter mới: assert không có
  `app`/`logger`/`platform` trong `vars()`, assert **không framework nào**
  (tập `FRAMEWORKS` của `test_architecture_boundaries.py`) xuất hiện trong
  `sys.modules`, rồi mới `compose()` và assert idempotent. Một test thứ hai
  pin package lazy (`build_container`/`create_app` load theo truy cập,
  tên lạ raise `AttributeError` cả trong lẫn ngoài process).
- **Test theo import dời.** Ba chỗ patch theo vị trí cũ được chuyển sang vị
  trí thật: `server.load_model/get_backend/reload_model` → `ai_assistant.llm.*`,
  `server.ChatbotAgent` → `orchestration.specialists`, `server.build_a2a_router`
  / `set_remote_specialist_lookup` → adapter/supervisor. Hành vi pin giữ
  nguyên (load vẫn gọi, chatbot vẫn singleton, router vẫn mount sau guard).
- **Full strict, với một ngoại lệ di động-môi-trường.**
  `python -m mypy src/ai_assistant --strict` đã 0 lỗi trên 135 files nên
  `[tool.mypy]` chuyển sang `strict = true`. Ngoại lệ duy nhất: 3 module chạm
  SDK (`observability.metrics`, `rag.embeddings`, `rag.loaders`) được nới
  `warn_return_any`/`disallow_subclassing_any`, vì typing của SDK bên thứ ba
  khác nhau giữa full-deps local và minimal `requirements-test.txt` trên CI
  (import thiếu/unstubbed suy biến thành `Any` ở CI trong khi local có kiểu
  chính xác — CI từng rớt đúng 3 lỗi này, không lỗi nào khác). Mọi flag
  strict còn lại giữ cho cả 3 module, và full strict giữ cho 132 files kia.
  Giữ override `adapters.mcp` (`warn_unused_ignores = false`, phụ thuộc phiên
  bản mcp). SCORECARD ghi `strict = true` kèm ngoại lệ này.
- **Mutation 38 → 39.** Thêm mutant cho nhánh fail-closed mới của package
  lazy (unknown attribute trả `None` thay vì raise): test in-process bắt.
- **Mutant recompose treo thay vì fail — pin bằng timeout.**
  Phá guard idempotency (`if _COMPOSED: return`) biến back-edge
  `compose() → _register_optional_protocols() → compose()` thành rebuild vô
  hạn (mỗi vòng ~1.6s IO mở log/store, quan sát treo >600s), nên suite sở hữu
  không bao giờ xong. Harness có thêm `timeout_seconds` + `kill_on_timeout`
  (opt-in đúng một mutant này): budget 180s so với ~25s khi chưa mutate
  (~7×) nên máy chậm không thể giả kill — timeout ở đây chính là bằng chứng
  guard chịu tải. CI job `test` nâng `timeout-minutes` 15 → 20 hữu hạn để
  chứa worst-case này.
