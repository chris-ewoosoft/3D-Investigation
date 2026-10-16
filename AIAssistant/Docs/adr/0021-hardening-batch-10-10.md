# ADR 0021 — 10/10 hardening batch: sandbox, redaction, discovery, VCS, image, contracts

Đợt thắt chặt đưa review 9.0/10 lên 10/10 mà không phá vỡ các pin hiện có.
Mỗi mục dưới đây là fail-closed và có mutant + test sở hữu.

- **Sandbox: `${...}` + confinement cho `python`/`pytest`.**
  `_FORBIDDEN_METACHAR` thêm `"${"` — `"$("` một mình bỏ lọt `${VAR}`/`$(( ))`.
  `_script_path_rejection()` mới confine mọi positional trông như path của
  `python`/`pytest` vào `cwd` qua `_path_escapes_workspace()` (trước đây chỉ
  `cmake` được confine; `python /tmp/evil.py` lọt). Hook trong
  `_subcommand_rejection()` sau cmake gate nên `git`/`cmake` không đổi hành vi.
- **Write deny-list mở rộng.** `_DENIED_WRITE_PATHS` thêm `tasks.sqlite`,
  `pending_agent_actions.json`, `agent_audit.jsonl`; substring gate thêm
  `.git`, `tasks.sqlite` và hai file durable trên — `src/.git/config` không
  còn ghi được dù nằm dưới allow-list root.
- **Redaction một owner, scrub cả value.**
  `SENSITIVE_KEYS` thêm `session_id`/`request_id`/`idempotency_key`/`context_id`
  (chống join session qua log). `redact_value()` pass string qua `scrub_pii()`
  nên secret dán dưới key thường (`query`/`content`) vẫn bị mask trong
  tool events/A2A/task events; `has_sensitive_keys()` giữ nguyên key-based để
  egress refusal không over-block query thường chứa email.
- **Discovery fail-closed.** `discover_remote_agents()` chỉ fetch `http(s)`,
  body bound `_DISCOVERY_MAX_BYTES` (1 MiB như transport), JSON lỗi/non-object
  bỏ qua thay vì raise trong `try` (TRY301), skill không phải dict bỏ qua, URL
  advertised chỉ giữ khi nằm dưới base (cho phép `/a2a` suffix, chặn host lạ).
  Dispatch vẫn do `TrustedA2AClient` enforce allowlist.
- **VCS qua cổng sandbox.** `vcs_tools._run_git()` validate
  `_executable_rejection` + `_subcommand_rejection`, resolve binary qua PATH
  (`_resolve_executable`), chạy absolute hit với `encoding=utf-8`. Không còn
  `subprocess.run(["git", ...])` trực tiếp. Test mock `_resolve_executable`
  nên hermetic.
- **Image multi-stage + non-root.** `Dockerfile` builder (`--prefix=/install`)
  + runtime chỉ copy site-packages + source, `pip install -e --no-deps` lại
  pointer, `useradd appuser`, `chown /app`, `USER appuser`. Không ship weights.
- **Contract thứ 8.** `pyproject.toml` thêm
  `orchestration → adapters` forbidden (AST twin đã có từ trước
  `test_orchestration_does_not_import_any_adapter`; guard `adapters.http` hẹp
  giữ lại vì message sắc hơn).
- **Mutation 32 → 38.** Fix 3 stale anchor (sandbox deny dòng mới, models
  `dict[str, Any]`, a2a_client signature có annotation) và gài 6 mutant mới
  cho đúng 6 guard trên; `38/38 killed` đo cùng ngày với coverage
  `8359 stmts / 2186 branches / 1791 tests`.
