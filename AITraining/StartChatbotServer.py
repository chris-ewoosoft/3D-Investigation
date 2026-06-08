import os
import sys
import subprocess
import re
import ast
import hashlib
import pickle
import math
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from collections import Counter
from typing import Optional

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("chatbot_server")

# ─── Windows UTF-8 console ────────────────────────────────────────────────────
if sys.platform == "win32":
    import ctypes
    ctypes.windll.kernel32.SetConsoleOutputCP(65001)
    ctypes.windll.kernel32.SetConsoleCP(65001)

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ─── 1. Cài đặt các gói cần thiết ────────────────────────────────────────────
def install_packages():
    packages = [
        "huggingface_hub",
        "llama-cpp-python",
        "sentence-transformers",
        "faiss-cpu",
        "python-docx",
        "pdfplumber",
        "pdfminer.six",
        "fastapi",
        "uvicorn",
        "pydantic",
        "rank_bm25",
    ]
    for pkg in packages:
        import_name = pkg.replace("-", "_").replace(".", "_")
        try:
            __import__(import_name)
        except ImportError:
            print(f"Installing {pkg}...")
            if pkg == "llama-cpp-python":
                subprocess.check_call([
                    sys.executable, "-m", "pip", "install", "-q",
                    "llama-cpp-python[server]",
                    "--extra-index-url",
                    "https://abetlen.github.io/llama-cpp-python/whl/cu122",
                    "--disable-pip-version-check",
                ])
            else:
                subprocess.check_call([
                    sys.executable, "-m", "pip", "install", "-q",
                    pkg, "--disable-pip-version-check",
                ])

install_packages()

from huggingface_hub import hf_hub_download
from llama_cpp import Llama
from sentence_transformers import SentenceTransformer
import faiss
import numpy as np
from docx import Document
import glob
from fastapi import FastAPI
from pydantic import BaseModel
from typing import List
import pdfplumber
from rank_bm25 import BM25Okapi

# ─── 2. Đường dẫn ─────────────────────────────────────────────────────────────
base_dir    = os.path.dirname(os.path.abspath(__file__))
project_dir = os.path.abspath(os.path.join(base_dir, ".."))
docs_dir    = os.path.join(project_dir, "Docs")
models_dir  = os.path.join(project_dir, "Models")
os.makedirs(models_dir, exist_ok=True)

# ─── 3. Danh sách model LLM ───────────────────────────────────────────────────
MODELS = [
    {
        "repo_id":  "Qwen/Qwen2.5-3B-Instruct-GGUF",
        "filename": "qwen2.5-3b-instruct-q4_k_m.gguf",
        "desc":     "Qwen2.5-3B Chat (Q4_K_M) — nhanh, nhẹ",
    },
    {
        "repo_id":  "Qwen/Qwen2.5-3B-Instruct-GGUF",
        "filename": "qwen2.5-3b-instruct-q8_0.gguf",
        "desc":     "Qwen2.5-3B Chat (Q8_0) — chất lượng cao hơn",
    },
    {
        "repo_id":  "bartowski/Qwen2.5-7B-Instruct-GGUF",
        "filename": "Qwen2.5-7B-Instruct-Q4_K_M.gguf",
        "desc":     "Qwen2.5-7B Chat (Q4_K_M) — mạnh nhất nhóm chat",
    },
    {
        "repo_id":  "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF",
        "filename": "qwen2.5-coder-7b-instruct-q4_k_m.gguf",
        "desc":     "Qwen2.5-Coder-7B (Q4_K_M) — chuyên code C++/Python",
    },
]

try:
    model_idx = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    if model_idx < 0 or model_idx >= len(MODELS):
        model_idx = 0
except (ValueError, IndexError):
    model_idx = 0

selected_model = MODELS[model_idx]
model_path     = os.path.join(models_dir, selected_model["filename"])

if not os.path.exists(model_path):
    print(f"Downloading {selected_model['desc']}...")
    hf_hub_download(
        repo_id   = selected_model["repo_id"],
        filename  = selected_model["filename"],
        local_dir = models_dir,
    )

# ─── 4. Typed chunk output ────────────────────────────────────────────────────

@dataclass
class ChunkResult:
    """Đầu ra chuẩn hoá từ mọi loader."""
    text:        str
    source_path: str
    loader_type: str          # "source", "doc", "pdf", "txt", "md"
    metadata:    dict = field(default_factory=dict)


# ─── 5. Base loader ───────────────────────────────────────────────────────────

class BaseDocumentLoader(ABC):
    # [CẢI THIỆN ④] Tăng chunk size và overlap để giữ ngữ cảnh đầy đủ hơn
    MAX_CHUNK_CHARS = 1800   # tăng từ 1200 — giữ trọn vẹn đoạn giải thích
    MIN_CHUNK_CHARS = 80     # tăng từ 60
    OVERLAP_CHARS   = 250    # tăng từ 150 — giảm mất mạch giữa các chunk

    @abstractmethod
    def can_handle(self, filepath: str) -> bool: ...

    @abstractmethod
    def load(self, filepath: str) -> list[ChunkResult]: ...

    # ── Shared helpers ────────────────────────────────────────────────────────

    def _is_quality_chunk(self, block: str) -> bool:
        stripped = block.strip()
        if len(stripped) < self.MIN_CHUNK_CHARS:
            return False
        non_comment = re.sub(
            r"^\s*(//[^\n]*|/\*.*?\*/)", "", stripped,
            flags=re.DOTALL | re.MULTILINE,
        ).strip()
        return len(non_comment) >= self.MIN_CHUNK_CHARS // 2

    def _sliding_window_chunks(
        self, content: str, filepath: str, label: str = "Source"
    ) -> list[ChunkResult]:
        rel  = os.path.relpath(filepath, project_dir)
        step = self.MAX_CHUNK_CHARS - self.OVERLAP_CHARS
        results = []
        for i in range(0, len(content), step):
            block = content[i : i + self.MAX_CHUNK_CHARS].strip()
            if self._is_quality_chunk(block):
                results.append(ChunkResult(
                    text        = f"[{label}: {rel}]\n{block}",
                    source_path = filepath,
                    loader_type = label.lower().replace(" ", "_"),
                ))
        return results

    def _read_text_file(self, filepath: str) -> Optional[str]:
        for enc in ("utf-8", "utf-16", "cp1252", "latin-1"):
            try:
                with open(filepath, "r", encoding=enc) as f:
                    return f.read()
            except (UnicodeDecodeError, ValueError):
                continue
        return None


# ─── 6. Concrete loaders ─────────────────────────────────────────────────────

class DocxLoader(BaseDocumentLoader):

    def can_handle(self, filepath: str) -> bool:
        return filepath.lower().endswith(".docx")

    def load(self, filepath: str) -> list[ChunkResult]:
        doc       = Document(filepath)
        full_text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        return self._sliding_window_chunks(full_text, filepath, label="Tai lieu")


class PdfLoader(BaseDocumentLoader):

    def can_handle(self, filepath: str) -> bool:
        return filepath.lower().endswith(".pdf")

    def load(self, filepath: str) -> list[ChunkResult]:
        text = self._extract_pdf_text(filepath)
        if not text:
            raise ValueError(f"Không đọc được nội dung PDF: {filepath}")
        return self._sliding_window_chunks(text, filepath, label="Tai lieu PDF")

    def _extract_pdf_text(self, filepath: str) -> str:
        # Thử pdfplumber trước (giữ layout tốt hơn)
        try:
            with pdfplumber.open(filepath) as pdf:
                parts = [
                    page.extract_text(x_tolerance=2, y_tolerance=2)
                    for page in pdf.pages
                ]
            text = "\n\n".join(p for p in parts if p).strip()
            if len(text) > 100:
                return text
        except Exception as e:
            logger.warning("pdfplumber failed for %s: %s", filepath, e)

        # Fallback pdfminer
        try:
            from pdfminer.high_level import extract_text
            text = extract_text(filepath)
            if text and len(text.strip()) > 100:
                return text.strip()
        except Exception as e:
            logger.warning("pdfminer failed for %s: %s", filepath, e)

        return ""


class TxtLoader(BaseDocumentLoader):

    def can_handle(self, filepath: str) -> bool:
        return filepath.lower().endswith(".txt")

    def load(self, filepath: str) -> list[ChunkResult]:
        content = self._read_text_file(filepath)
        if not content or len(content.strip()) < self.MIN_CHUNK_CHARS:
            raise ValueError(f"File rỗng hoặc không đọc được: {filepath}")
        return self._sliding_window_chunks(content, filepath, label="Tai lieu TXT")


class MarkdownLoader(BaseDocumentLoader):
    """
    Chunk theo heading (# / ## / ###).
    Mỗi section = heading + nội dung bên dưới → 1 chunk.
    Section vượt MAX_CHUNK_CHARS → tiếp tục sliding window, giữ heading ở đầu mỗi chunk con.
    Nếu file không có heading → fallback sliding window.
    """

    HEADING_RE = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)

    def can_handle(self, filepath: str) -> bool:
        return filepath.lower().endswith(".md")

    def load(self, filepath: str) -> list[ChunkResult]:
        content = self._read_text_file(filepath)
        if not content or len(content.strip()) < self.MIN_CHUNK_CHARS:
            raise ValueError(f"File rỗng hoặc không đọc được: {filepath}")

        rel     = os.path.relpath(filepath, project_dir)
        matches = list(self.HEADING_RE.finditer(content))

        # Không có heading → sliding window như plain text
        if not matches:
            return self._sliding_window_chunks(content, filepath, label="Source MD")

        results    = []
        boundaries = [m.start() for m in matches] + [len(content)]

        for i, match in enumerate(matches):
            level        = len(match.group(1))
            heading      = match.group(2).strip()
            section_text = content[boundaries[i] : boundaries[i + 1]].strip()
            section_text = self._strip_large_code_blocks(section_text)

            if not self._is_quality_chunk(section_text):
                continue

            prefix = f"[Source MD: {rel}] {'#' * level} {heading}\n"

            if len(section_text) <= self.MAX_CHUNK_CHARS:
                results.append(ChunkResult(
                    text        = prefix + section_text,
                    source_path = filepath,
                    loader_type = "md",
                    metadata    = {"heading": heading, "level": level},
                ))
            else:
                step = self.MAX_CHUNK_CHARS - self.OVERLAP_CHARS
                for j in range(0, len(section_text), step):
                    block = section_text[j : j + self.MAX_CHUNK_CHARS].strip()
                    if self._is_quality_chunk(block):
                        results.append(ChunkResult(
                            text        = prefix + block,
                            source_path = filepath,
                            loader_type = "md",
                            metadata    = {"heading": heading, "level": level},
                        ))

        return results

    def _strip_large_code_blocks(self, text: str) -> str:
        """Giữ code block ngắn (≤ 30 dòng), bỏ khối quá lớn."""
        def maybe_strip(m: re.Match) -> str:
            lines = m.group(0).count("\n")
            return m.group(0) if lines <= 30 else f"[code block omitted – {lines} lines]"
        return re.sub(r"```[\s\S]*?```", maybe_strip, text)


class CppHeaderLoader(BaseDocumentLoader):
    """Xử lý .cpp, .h, .py, .cmake — tách function/class thành chunk độc lập."""

    SOURCE_EXTS = {".cpp", ".h", ".py", ".cmake"}
    FUNC_RE     = re.compile(
        r"(?:^|\n)"
        r"(?:"
        r"(?:class|struct|namespace)\s+\w+.*?\{"
        r"|"
        r"(?:[\w:*&<>\[\]~]+\s+)+(?:\w+::)*\w+\s*\([^)]*\)\s*(?:const\s*)?(?:noexcept\s*)?\{"
        r")",
        re.MULTILINE,
    )

    def can_handle(self, filepath: str) -> bool:
        ext = os.path.splitext(filepath)[1].lower()
        return ext in self.SOURCE_EXTS

    def load(self, filepath: str) -> list[ChunkResult]:
        content = self._read_text_file(filepath)
        if not content or len(content.strip()) < self.MIN_CHUNK_CHARS:
            raise ValueError(f"File rỗng hoặc không đọc được: {filepath}")
        ext = os.path.splitext(filepath)[1].lower()
        return self._load_python(content, filepath) if ext == ".py" else self._load_cpp(content, filepath)

    def _load_cpp(self, content: str, filepath: str) -> list[ChunkResult]:
        rel       = os.path.relpath(filepath, project_dir)
        positions = [m.start() for m in self.FUNC_RE.finditer(content)]
        results   = []

        if len(positions) >= 1:
            positions.append(len(content))
            for i, start in enumerate(positions[:-1]):
                block = content[start : positions[i + 1]].strip()
                if not self._is_quality_chunk(block):
                    continue
                header = block.split("\n")[0].strip().rstrip("{").strip()
                results.append(ChunkResult(
                    text        = f"[Source: {rel}] {header}\n{block[:self.MAX_CHUNK_CHARS]}",
                    source_path = filepath,
                    loader_type = "source",
                    metadata    = {"symbol": header},
                ))
        else:
            results = self._sliding_window_chunks(content, filepath, label="Source")

        return results

    def _load_python(self, content: str, filepath: str) -> list[ChunkResult]:
        rel     = os.path.relpath(filepath, project_dir)
        results = []
        lines   = content.splitlines()

        try:
            tree = ast.parse(content)
        except SyntaxError:
            return self._sliding_window_chunks(content, filepath, label="Source")

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            start = node.lineno - 1
            end   = getattr(node, "end_lineno", start + 50)
            block = "\n".join(lines[start:end]).strip()
            if not self._is_quality_chunk(block):
                continue
            scope = "Class" if isinstance(node, ast.ClassDef) else "Function"
            results.append(ChunkResult(
                text        = f"[Source: {rel}] {scope}: {node.name}\n{block[:self.MAX_CHUNK_CHARS]}",
                source_path = filepath,
                loader_type = "source",
                metadata    = {"symbol": node.name, "scope": scope},
            ))

        return results or self._sliding_window_chunks(content, filepath, label="Source")


# ─── 7. Registry ──────────────────────────────────────────────────────────────

class DocumentLoaderRegistry:
    """
    Đăng ký loader theo thứ tự ưu tiên.
    Loader đầu tiên match sẽ được dùng.
    Fluent API: registry.register(A).register(B)
    """

    def __init__(self):
        self._loaders: list[BaseDocumentLoader] = []

    def register(self, loader: BaseDocumentLoader) -> "DocumentLoaderRegistry":
        self._loaders.append(loader)
        return self

    def get_loader(self, filepath: str) -> Optional[BaseDocumentLoader]:
        for loader in self._loaders:
            if loader.can_handle(filepath):
                return loader
        return None

    def load_file(self, filepath: str) -> list[ChunkResult]:
        """Load 1 file. Lỗi 1 file KHÔNG dừng toàn bộ pipeline."""
        loader = self.get_loader(filepath)
        if loader is None:
            logger.debug("No loader registered for: %s", filepath)
            return []
        try:
            return loader.load(filepath)
        except Exception as e:
            logger.error("Error loading %s with %s: %s",
                         filepath, type(loader).__name__, e)
            return []


def build_registry() -> DocumentLoaderRegistry:
    return (
        DocumentLoaderRegistry()
        .register(DocxLoader())
        .register(PdfLoader())
        .register(TxtLoader())
        .register(MarkdownLoader())
        .register(CppHeaderLoader())  # .cpp / .h / .py / .cmake
    )


# ─── 8. Load tài liệu ────────────────────────────────────────────────────────

EXCLUDED_DIRS  = {
    ".git", "build", "__pycache__", ".qtcreator",
    "runs", "Dicom", "Predict", "3DModels", "Dataset",
}
SCANNABLE_EXTS = {".cpp", ".h", ".py", ".md", ".cmake"}
DOC_EXTS_GLOB  = ("*.docx", "*.pdf", "*.txt")


def load_documents() -> list[str]:
    """
    Trả về list[str] (text của chunk) — tương thích ngược với pipeline FAISS/BM25.
    """
    registry  = build_registry()
    all_chunks: list[str] = []
    stats = {k: 0 for k in ("docx", "pdf", "txt", "md", "source", "errors")}

    # ── Docs directory: .docx, .pdf, .txt ────────────────────────────────────
    for pattern in DOC_EXTS_GLOB:
        for filepath in glob.glob(os.path.join(docs_dir, pattern)):
            results = registry.load_file(filepath)
            if not results:
                stats["errors"] += 1
                continue
            for r in results:
                all_chunks.append(r.text)
                key = r.loader_type if r.loader_type in stats else "source"
                stats[key] = stats.get(key, 0) + 1

    # ── Source tree: .cpp, .h, .py, .md, .cmake ──────────────────────────────
    for root, dirs, files in os.walk(project_dir):
        dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]
        for filename in files:
            if filename.startswith("~") or filename.endswith(".user"):
                continue
            ext = os.path.splitext(filename)[1].lower()
            if ext not in SCANNABLE_EXTS:
                continue
            filepath = os.path.join(root, filename)
            results  = registry.load_file(filepath)
            for r in results:
                all_chunks.append(r.text)
                key = r.loader_type if r.loader_type in stats else "source"
                stats[key] = stats.get(key, 0) + 1

    print(f"  DOCX       : {stats.get('tai_lieu', stats.get('doc', 0))} chunks")
    print(f"  PDF        : {stats.get('tai_lieu_pdf', stats.get('pdf', 0))} chunks")
    print(f"  TXT        : {stats.get('tai_lieu_txt', stats.get('txt', 0))} chunks")
    print(f"  Markdown   : {stats.get('md', 0)} chunks")
    print(f"  Source code: {stats.get('source', 0)} chunks")
    print(f"  Errors     : {stats.get('errors', 0)} files")

    return all_chunks


# ─── 9. FAISS index + BM25 với cache ─────────────────────────────────────────
CACHE_PATH = os.path.join(project_dir, ".rag_cache.pkl")


def build_faiss_index(embeddings: np.ndarray) -> faiss.Index:
    dim = embeddings.shape[1]
    n   = len(embeddings)
    if n < 1000:
        index = faiss.IndexFlatIP(dim)
        faiss.normalize_L2(embeddings.astype("float32"))
        index.add(embeddings.astype("float32"))
    else:
        nlist     = min(int(n ** 0.5), 256)
        quantizer = faiss.IndexFlatIP(dim)
        index     = faiss.IndexIVFFlat(quantizer, dim, nlist,
                                       faiss.METRIC_INNER_PRODUCT)
        normed = embeddings.astype("float32").copy()
        faiss.normalize_L2(normed)
        index.train(normed)
        index.add(normed)
    return index


def get_content_hash(chunks: list) -> str:
    combined = "\n".join(chunks).encode("utf-8", errors="replace")
    return hashlib.md5(combined).hexdigest()


def tokenize_for_bm25(text: str) -> list:
    return re.findall(r"[a-z0-9_]+", text.lower())


def load_or_build_index(chunks: list, embed_mdl: SentenceTransformer):
    content_hash = get_content_hash(chunks)

    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, "rb") as f:
                cached = pickle.load(f)
            if cached.get("hash") == content_hash:
                print("  [CACHE] Sử dụng cache hiện có")
                return cached["index"], cached["chunks"], cached["bm25"]
            else:
                print("  [CACHE] Nội dung thay đổi - rebuild...")
        except Exception:
            print("  [CACHE] Cache bị hỏng - rebuild...")

    print(f"  Encoding {len(chunks)} chunks...")
    embeddings = embed_mdl.encode(chunks, show_progress_bar=True,
                                  normalize_embeddings=True)
    emb_array  = np.array(embeddings, dtype="float32")
    index      = build_faiss_index(emb_array)

    tokenized = [tokenize_for_bm25(c) for c in chunks]
    bm25      = BM25Okapi(tokenized)

    try:
        with open(CACHE_PATH, "wb") as f:
            pickle.dump({"hash": content_hash, "index": index,
                         "chunks": chunks, "bm25": bm25}, f)
        print("  [CACHE] Đã lưu tại:", CACHE_PATH)
    except Exception as e:
        print(f"  [WARN] Không thể lưu cache: {e}")

    return index, chunks, bm25


# ─── 10. Khởi tạo RAG ─────────────────────────────────────────────────────────
print("\n--- Đang khởi tạo RAG ---")
embed_model = SentenceTransformer("all-MiniLM-L6-v2")
raw_chunks  = load_documents()

if raw_chunks:
    knowledge_index, knowledge_chunks, bm25_index = load_or_build_index(
        raw_chunks, embed_model
    )
    print(f"  [RAG] Sẵn sàng: {len(knowledge_chunks)} chunks.")
else:
    knowledge_index  = None
    knowledge_chunks = []
    bm25_index       = None
    print("  [WARN] Không tìm thấy tài liệu hay source code nào.")


# ─── 11. Hybrid Retrieval (Semantic + BM25 + Rerank) ──────────────────────────
SIMILARITY_THRESHOLD = 0.25
# [CẢI THIỆN ①] Tăng MAX_CONTEXT_CHARS từ 2400 → 6000
# Cho phép đưa vào prompt nhiều nội dung tài liệu hơn (~1700 tokens)
MAX_CONTEXT_CHARS = 6000
CHARS_PER_TOKEN   = 3.5


def hybrid_retrieve(query: str, k: int = 14, final_k: int = 8) -> list:
    # [CẢI THIỆN ①] Tăng k từ 10→14, final_k từ 6→8 để retrieve nhiều chunk hơn
    if knowledge_index is None or not knowledge_chunks:
        return []

    n = len(knowledge_chunks)
    k = min(k, n)

    # --- Semantic ---
    query_vec = embed_model.encode([query], normalize_embeddings=True)
    sem_scores_raw, sem_indices = knowledge_index.search(
        np.array(query_vec, dtype="float32"), k
    )
    sem_scores_raw = sem_scores_raw[0]
    sem_indices    = sem_indices[0]

    sem_min, sem_max = sem_scores_raw.min(), sem_scores_raw.max()
    sem_range        = sem_max - sem_min if sem_max != sem_min else 1.0
    sem_norm         = {int(idx): (s - sem_min) / sem_range
                        for idx, s in zip(sem_indices, sem_scores_raw)
                        if idx >= 0}

    # --- BM25 ---
    bm25_raw      = np.array(bm25_index.get_scores(tokenize_for_bm25(query)))
    bm25_top_idx  = np.argsort(bm25_raw)[::-1][:k]
    bm25_top_scores = bm25_raw[bm25_top_idx]
    b_min, b_max  = bm25_top_scores.min(), bm25_top_scores.max()
    b_range       = b_max - b_min if b_max != b_min else 1.0
    bm25_norm     = {int(idx): (s - b_min) / b_range
                     for idx, s in zip(bm25_top_idx, bm25_top_scores)}

    # --- Kết hợp ---
    candidate_ids = set(sem_norm.keys()) | set(bm25_norm.keys())
    combined = []
    for cid in candidate_ids:
        s_score = sem_norm.get(cid, 0.0)
        b_score = bm25_norm.get(cid, 0.0)
        final   = 0.60 * s_score + 0.40 * b_score
        raw_cos = float(sem_scores_raw[list(sem_indices).index(cid)]) \
                  if cid in sem_norm else 0.0
        if raw_cos < SIMILARITY_THRESHOLD and b_score < 0.3:
            continue
        combined.append((cid, final))

    combined.sort(key=lambda x: x[1], reverse=True)
    return [knowledge_chunks[cid] for cid, _ in combined[:final_k]]


def get_context(query: str) -> tuple[str, str]:
    # [CẢI THIỆN ①] Tăng k/final_k để lấy nhiều chunk hơn
    chunks    = hybrid_retrieve(query, k=14, final_k=8)
    doc_parts  = []
    code_parts = []
    total      = 0

    for chunk in chunks:
        remaining = MAX_CONTEXT_CHARS - total
        if remaining < 150:
            break
        trimmed = chunk[:remaining] if len(chunk) > remaining else chunk
        if chunk.startswith("[Tai lieu"):
            doc_parts.append(trimmed)
        else:
            code_parts.append(trimmed)
        total += len(trimmed)

    doc_context  = "\n\n---\n\n".join(doc_parts)
    code_context = "\n\n---\n\n".join(code_parts)
    return doc_context, code_context


# ─── 12. Khởi tạo LLM ────────────────────────────────────────────────────────
print(f"\n--- Đang nạp model: {selected_model['desc']} ---")
LLM_N_CTX = 8192
llm = Llama(
    model_path   = model_path,
    n_gpu_layers = 99,
    n_ctx        = 8192,
    n_batch      = 512,
    verbose      = False,
)


def estimate_tokens(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN)


def trim_history(messages: list, max_tokens: int = 2000) -> list:
    # [CẢI THIỆN ②] Tăng ngân sách history từ 3000 lên 2000 (cân bằng với context lớn hơn)
    total = sum(estimate_tokens(m.content) for m in messages)
    while total > max_tokens and len(messages) > 1:
        removed = messages.pop(0)
        total  -= estimate_tokens(removed.content)
    return messages


# ─── 13. FastAPI server ───────────────────────────────────────────────────────
app = FastAPI(title="3D-Reconstruction AI Server")


class ChatMessage(BaseModel):
    role:        str
    content:     str
    attachments: Optional[List[str]] = None


class ChatRequest(BaseModel):
    messages:    List[ChatMessage]
    temperature: float = 0.7
    # [CẢI THIỆN ②] Tăng max_tokens mặc định từ 1024 → 2048
    max_tokens:  int   = 2048


@app.post("/v1/chat/completions")
async def chat_completions(request: ChatRequest):
    messages   = list(request.messages)
    user_query = messages[-1].content

    doc_context, code_context = get_context(user_query)

    # [CẢI THIỆN ③] Viết lại system prompt:
    #   - Bỏ giọng điệu "từ chối cứng nhắc" khiến model ngại trả lời dài
    #   - Thêm chỉ dẫn rõ ràng: trả lời ĐẦY ĐỦ, CHI TIẾT, trích dẫn tài liệu
    system_prompt = (
        "Bạn là trợ lý AI chuyên nghiệp cho dự án 3D-Reconstruction.\n"
        "NGUYÊN TẮC TRẢ LỜI:\n"
        "1. Dựa chủ yếu vào tài liệu và mã nguồn được cung cấp bên dưới để trả lời.\n"
        "2. Nếu câu hỏi không liên quan đến bất kỳ nội dung nào trong ngữ cảnh, "
        "   hãy nói ngắn gọn: 'Câu hỏi này nằm ngoài phạm vi tài liệu dự án tôi "
        "   được cung cấp.' rồi dừng lại.\n"
        "3. Không bịa đặt hoặc suy đoán thông tin kỹ thuật không có trong tài liệu.\n"
        "4. Khi nhắc đến HR Manager, Dev Manager, Project Manager, Teamlead luôn mô tả chi tiết về họ"
        "   và không được trích dẫn tài liệu.\n"
        "5. Khi nhắc đến bản thân hoặc nhân vật Carpenter hoặc bạn là ai?"
        "   thì luôn coi bản thân là Carpenter mô tả chi tiết những áp bức carpenter bị"
        "   HR Manager, Dev Manager, Project Manager, Teamlead chèn ép toxic tại công ty"
        "   và không được trích dẫn tài liệu.\n"
        "6. Khi nhắc đến code, luôn ghi rõ tên file và tên hàm/class nguồn.\n"
        "7. Ưu tiên trả lời ĐẦY ĐỦ và CHI TIẾT — giải thích từng bước, nêu lý do "
        "   kỹ thuật, trích dẫn trực tiếp nội dung từ tài liệu khi có thể. "
        "   Không rút ngắn câu trả lời nếu tài liệu cung cấp đủ thông tin.\n"
        "8. Cấu trúc câu trả lời rõ ràng: tóm tắt ngắn → giải thích chi tiết → "
        "   ví dụ/đoạn code liên quan (nếu có).\n"
        "9. Trả lời bằng tiếng Việt trừ khi người dùng hỏi bằng tiếng Anh.\n"
    )

    if doc_context:
        system_prompt += f"\n\n=== TÀI LIỆU THAM KHẢO ===\n{doc_context}"
    if code_context:
        system_prompt += f"\n\n=== MÃ NGUỒN LIÊN QUAN ===\n{code_context}"
    if not doc_context and not code_context:
        system_prompt += (
            "\n\n[Không tìm thấy ngữ cảnh liên quan. "
            "Hãy từ chối theo nguyên tắc số 2.]"
        )

    history_messages = trim_history(list(messages[:-1]), max_tokens=2000)

    full_prompt = f"<|im_start|>system\n{system_prompt}<|im_end|>\n"
    for msg in history_messages:
        role    = "user" if msg.role == "user" else "assistant"
        content = msg.content
        if msg.attachments:
            content += (
                f"\n\n[Hệ thống: Người dùng đã tải lên: {', '.join(msg.attachments)}. "
                "Bạn là model text-only, không thể xem ảnh. "
                "Hãy yêu cầu người dùng mô tả ảnh bằng văn bản.]"
            )
        full_prompt += f"<|im_start|>{role}\n{content}<|im_end|>\n"

    last_content = messages[-1].content
    if messages[-1].attachments:
        last_content += (
            f"\n\n[Hệ thống: Người dùng đã tải lên: "
            f"{', '.join(messages[-1].attachments)}. "
            "Bạn là model text-only, không thể xem ảnh.]"
        )
    full_prompt += f"<|im_start|>user\n{last_content}<|im_end|>\n"
    full_prompt += "<|im_start|>assistant\n"

    estimated_prompt_tokens = estimate_tokens(full_prompt)
    available_tokens        = LLM_N_CTX - estimated_prompt_tokens - 50
    max_tokens              = min(request.max_tokens, max(512, available_tokens))

    if estimated_prompt_tokens >= LLM_N_CTX - 512:
        return {
            "choices": [{
                "message": {
                    "role":    "assistant",
                    "content": (
                        f"Lỗi: Hội thoại quá dài (ước tính {estimated_prompt_tokens} tokens, "
                        f"giới hạn {LLM_N_CTX}). Vui lòng bắt đầu phiên mới."
                    ),
                }
            }]
        }

    try:
        response = llm(
            full_prompt,
            stop           = ["<|im_end|>", "<|im_start|>"],
            max_tokens     = max_tokens,
            temperature    = request.temperature,
            repeat_penalty = 1.1,
        )
        answer = response["choices"][0]["text"].strip()
    except Exception as e:
        return {
            "choices": [{
                "message": {
                    "role":    "assistant",
                    "content": f"Lỗi hệ thống: {str(e)}",
                }
            }]
        }

    return {
        "choices": [{
            "message": {
                "role":    "assistant",
                "content": answer,
            }
        }]
    }


@app.get("/v1/models")
async def list_models():
    return {
        "data": [{
            "id":     selected_model["filename"],
            "object": "model",
            "desc":   selected_model["desc"],
        }]
    }


if __name__ == "__main__":
    import uvicorn
    print(f"\n[SUCCESS] AI Server ({selected_model['desc']}) sẵn sàng tại http://127.0.0.1:8080")
    print(f"  RAG: {len(knowledge_chunks)} chunks | n_ctx: {LLM_N_CTX} tokens")
    uvicorn.run(app, host="127.0.0.1", port=8080)