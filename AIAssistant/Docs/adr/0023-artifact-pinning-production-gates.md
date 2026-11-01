# ADR 0023 — Pin artifact & cổng production fail-closed

Review (8.3/10) chỉ ra bốn lỗ hổng chuỗi cung ứng/vận hành: tải model từ
`main` không pin, không lockfile/SBOM, sandbox mặc định runtime local, và
checkpoint mặc định rơi về `MemorySaver` **im lặng**. Cả bốn đều cùng một
hình dạng — degrade mà không nói — nên batch này biến từng cái thành một pin
hoặc một cổng chặn theo ADR 0015: không còn claim suông.

- **Pin GGUF theo commit + SHA-256.** `ModelDefinition` thêm
  `revision`/`sha256`/`mmproj_revision`/`mmproj_sha256`; `config/models.toml`
  và `_DEFAULT_MODELS`/`_FALLBACK` giữ cùng cặp giá trị. Nguồn pin: commit
  Hugging Face + `lfs.oid` từ tree API, rồi đối chiếu với SHA-256 thật của
  **cả 5 file GGUF local** — khớp toàn bộ (`scripts/verify_models.py` →
  `OK: 5 verified, 1 not downloaded yet, 0 mismatched`). Fallback 3B (file
  không nằm trong `Models/`) cũng được xác minh độc lập: bản tải về dùng cho
  eval có SHA-256 đúng bằng pin `9c9f56a3…`. Không dùng
  `x-linked-etag`: đó là hash Xet, không phải SHA-256 nội dung.
  `hf_hub_download(..., revision=…)`; file local lệch pin bị xoá và tải lại
  **một lần**, lệch lần hai raise `ModelIntegrityError` — không load weight
  chưa xác minh.
- **Lockfile (version + hash, được enforce).** Hai lock sinh bằng
  `uv pip compile <reqs> --python-version 3.11 --universal --generate-hashes`:
  `requirements.lock` (171 distribution runtime, giữ marker `faiss-cpu` win /
  `faiss-gpu` non-win; 3 941 hash) và `requirements-test.lock` (85
  distribution cho bộ test). Mỗi dòng mang toàn bộ `--hash=sha256:` index trả
  về, nên lệnh cài là `pip install --require-hashes -r <lock>` — pip **từ
  chối** artifact lệch digest. CI job `test` cài đúng
  `requirements-test.lock` bằng `--require-hashes`, nên pin giờ là thứ được
  *enforce* trên mọi push, không chỉ ghi chú. Runtime lock cùng generator +
  parse sạch (`pip --require-hashes --dry-run`, exit 0) và đã tải thật xác
  minh hash trên mẫu 6 package; không cài full trong CI vì kéo theo
  torch/nvidia hàng GB. Hash ở đây chứng minh *danh tính artifact*, không
  phải provenance/chữ ký.
- **SBOM.** `scripts/generate_sbom.py` (stdlib) xuất CycloneDX 1.5: các
  distribution từ lock **kèm hash SHA-256 từng artifact** + 6 artifact model
  với repo/commit/SHA-256; job `agent-evals.yml` ghi thành artifact
  `aiassistant-sbom`.
  `tests/unit/test_sbom_script.py` pin ba thứ: đọc pin từ **lock** (không
  phải range trong `requirements.txt`), hash gắn đúng component, và *cả hai*
  lock phải hash-pinned (mọi distribution ≥1 `sha256` 64 hex);
  `tests/unit/test_verify_models_script.py` pin pre-flight
  OK / MISMATCH (exit 1) / MISSING (`--require-all`).
- **Cổng sandbox.** `AGENT_SANDBOX_REQUIRE_ISOLATION=1` → `run()` từ chối
  runtime local với thông báo nói rõ cách bật docker; docker argv thêm
  `--cap-drop ALL` + `--security-opt no-new-privileges`. Desktop mặc định 0
  (không phá trải nghiệm single-user); mọi deployment dùng tool output không
  tin cậy phải bật 1.
- **Cổng checkpoint.** `AGENT_CHECKPOINT_REQUIRED=1` → backend memory/hỏng
  raise `RuntimeError` (kèm `from` nguyên nhân) thay vì rơi về `MemorySaver`
  im lặng; giá trị này đi qua cả ba adapter sqlite/postgres/redis. Mặc định 0
  cho desktop.
- **CI tải model theo revision.** `agent-evals.yml` tải fallback qua
  `resolve/<commit>` rồi `sha256sum -c` đúng cặp pin trong `models.toml`;
  cache key gắn revision — `main` di chuyển không đổi được số eval, cache hit
  cũng bị verify lại.
- **Bảo vệ bằng test:** `PinnedArtifactTests` + `MmprojRevisionTests` +
  `TrackedModelPinsTests` (`test_llm_backend`), isolation tests
  (`test_sandbox`), `RequiredCheckpointTests` (`test_checkpointing`),
  `test_sbom_script`, `test_verify_models_script`, `test_settings`,
  `test_lockfile_sync` (mọi direct requirement trong hai file
  `requirements*.txt` phải được lock tương ứng thỏa mãn — bump range mà quên
  regenerate lock là suite đỏ kèm tên requirement, không im lặng cài bộ cũ;
  checker tự có ca drift giả để không thể là no-op).
- **Hệ quả:** profile desktop giữ nguyên hành vi (mọi cổng là opt-in);
  production phải bật cổng — `.env.example` và `Docs/RUNBOOK.md` ghi cách bật,
  cách luân chuyển pin và cách xử lý khi cổng từ chối. Khoảng trống còn lại
  của chuỗi cung ứng là *provenance* (không chữ ký/attestation — hash chứng
  minh danh tính artifact, không chứng minh ai build nó), ghi nhận thay vì
  giả vờ đã có.
