# ruff: noqa: BLE001, S110, DTZ005, RUF012
"""
StartChatbotServer.py — 3D-Reconstruction AI Server v2.2
=========================================================

[v2.2] Cải tiến chất lượng RAG so với v2.1:
  [FIX-6]  BM25 tokenizer hỗ trợ tiếng Việt — regex cũ bỏ sót TOÀN BỘ từ Việt
  [FIX-7]  Cross-encoder re-ranking (tùy chọn) — tăng precision đáng kể
  [FIX-8]  Embedding model đa ngôn ngữ — paraphrase-multilingual-MiniLM-L12-v2
  [FIX-9]  Chunk nhỏ hơn (1200 chars) — embedding signal tập trung, ít nhiễu hơn
  [FIX-10] Phát hiện finish_reason="length" — cảnh báo và tự thử lại nếu bị cắt
  [FIX-11] Context formatting có số thứ tự + nhãn nguồn — model cite đúng hơn

[v2.1] Giữ nguyên:
  CHARS_PER_TOKEN=2.2, buffer=400 token, MAX_CONTEXT_CHARS=7500
  Sentence-aware chunking, source dedup ≤2/file, rule #8 system prompt

Cấu trúc thư mục:
  AITraining/
  ├── StartChatbotServer.py
  ├── requirements.txt
  ├── Cache/
  │   ├── faiss_index.bin
  │   ├── chunks.pkl
  │   ├── bm25.pkl
  │   └── metadata.json
  └── logs/
      └── server_YYYYMMDD_HHMMSS.log

LƯU Ý: v2.2 đổi embedding model và chunk size → xóa Cache/ để rebuild.
"""

# ─── 0. Bootstrap ─────────────────────────────────────────────────────────────
import ast
import base64
import ctypes
import gc
import glob
import hashlib
import json
import logging
import logging.handlers
import os
import pickle
import re
import sys
import threading
import time
import unicodedata
import warnings
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from datetime import datetime

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

try:
    from LangGraphAgent import LocalAgentGraph
    LANGGRAPH_AVAILABLE = True
    LANGGRAPH_IMPORT_ERROR = ""
except ImportError as error:
    LocalAgentGraph = None
    LANGGRAPH_AVAILABLE = False
    LANGGRAPH_IMPORT_ERROR = str(error)

if sys.platform == "win32":
    ctypes.windll.kernel32.SetConsoleOutputCP(65001)
    ctypes.windll.kernel32.SetConsoleCP(65001)

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
os.environ.setdefault("TF_ENABLE_ONEDNN_OPTS", "0")
USE_LANGGRAPH_AGENT = os.environ.get("USE_LANGGRAPH_AGENT", "1") != "0"

warnings.filterwarnings("ignore", category=UserWarning, module="transformers")
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", module="keras")

# ─── 1. Stdlib imports ────────────────────────────────────────────────────────
# ─── 2. Đường dẫn ─────────────────────────────────────────────────────────────
BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.abspath(os.path.join(BASE_DIR, ".."))
APP_DATA_DIR = os.environ.get("APP_DATA_DIR", PROJECT_DIR)
DOCS_DIR    = os.path.join(PROJECT_DIR, "Docs")
MODELS_DIR  = os.path.join(APP_DATA_DIR, "AITraining", "Models")
CACHE_DIR   = os.path.join(APP_DATA_DIR, "AITraining", "Cache")
LOGS_DIR    = os.path.join(APP_DATA_DIR, "AITraining", "logs")
EMBED_CACHE = os.path.join(CACHE_DIR, "embed_model")

for _d in (CACHE_DIR, LOGS_DIR, EMBED_CACHE, MODELS_DIR):
    os.makedirs(_d, exist_ok=True)
if not os.path.exists(DOCS_DIR):
    try:
        os.makedirs(DOCS_DIR, exist_ok=True)
    except PermissionError:
        pass

CACHE_INDEX    = os.path.join(CACHE_DIR, "faiss_index.bin")
CACHE_CHUNKS   = os.path.join(CACHE_DIR, "chunks.pkl")
CACHE_BM25     = os.path.join(CACHE_DIR, "bm25.pkl")
CACHE_METADATA = os.path.join(CACHE_DIR, "metadata.json")


def _safe_relpath(path: str, start: str) -> str:
    try:
        return os.path.relpath(path, start)
    except ValueError:
        return os.path.abspath(path)

# ─── 3. Cấu hình RAG — chỉnh tại đây ─────────────────────────────────────────
# [FIX-8] Đổi sang model đa ngôn ngữ — hỗ trợ tiếng Việt tốt hơn all-MiniLM-L6-v2
# Kích thước: ~470MB (so với ~80MB), nhưng độ chính xác retrieval tăng rõ rệt.
# Nếu muốn giữ model cũ (tiết kiệm RAM/disk): đổi lại "all-MiniLM-L6-v2" hoặc "paraphrase-multilingual-MiniLM-L12-v2"
EMBED_MODEL_NAME = "clip-ViT-B-32"
RAG_CACHE_VERSION = 3

# [FIX-7] Cross-encoder re-ranking — bật/tắt tùy tài nguyên
# True  = kết quả chính xác hơn, latency tăng ~100-300ms/request
# False = tắt hoàn toàn, hành vi như v2.1
USE_RERANKER    = True
RERANKER_MODEL  = "cross-encoder/ms-marco-MiniLM-L-6-v2"  # ~80MB
RERANKER_TOP_K  = 8    # Tăng lên 8 để lấy thêm context

# [FIX-9] Chunk nhỏ hơn → embedding signal tập trung, ít nhiễu
# 1200 thay vì 1800: mỗi chunk mang một ý chính, không pha trộn nhiều chủ đề
# LƯU Ý: thay đổi giá trị này buộc rebuild cache
ENABLE_VISION_LLM = os.environ.get("AI_ENABLE_VISION_LLM", "1").strip().lower() in {"1", "true", "yes", "on"}
ENABLE_RAG      = os.environ.get("AI_ENABLE_RAG", "1").strip().lower() in {"1", "true", "yes", "on"}
CHUNK_CHARS     = 1200
OVERLAP_CHARS   = 300  # Tăng lên 300 để giữ liên kết tiếng Việt

# v2.1 constants (giữ nguyên)
SIMILARITY_THRESHOLD = 0.25
MAX_CONTEXT_CHARS    = 9000 # Tăng lên 9000 để chứa đủ chi tiết tiếng Việt
CHARS_PER_TOKEN      = 2.2   # Việt+code, tránh underestimate
LLM_N_CTX            = 8192

# ─── 4. Logging ───────────────────────────────────────────────────────────────
def setup_logging():
    log_filename = os.path.join(
        LOGS_DIR, f"server_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    )
    fmt_console = logging.Formatter("%(asctime)s %(levelname)-8s %(message)s", "%H:%M:%S")
    fmt_file    = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                                    "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()

    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt_console)
    ch.setLevel(logging.INFO)
    root.addHandler(ch)

    fh = logging.handlers.RotatingFileHandler(
        log_filename, maxBytes=10*1024*1024, backupCount=5, encoding="utf-8"
    )
    fh.setFormatter(fmt_file)
    fh.setLevel(logging.DEBUG)
    root.addHandler(fh)

    for _n in ("httpx", "httpcore", "urllib3", "sentence_transformers",
               "huggingface_hub", "faiss", "uvicorn.access"):
        logging.getLogger(_n).setLevel(logging.WARNING)

    return logging.getLogger("chatbot_server"), log_filename

logger, LOG_FILE_PATH = setup_logging()

# ─── 5. Startup timer ─────────────────────────────────────────────────────────
_SERVER_START_TIME = time.monotonic()

@contextmanager
def startup_step(name: str):
    print(f"  ⏳  {name}...", end="", flush=True)
    t = time.monotonic()
    try:
        yield
    except Exception as e:
        elapsed = time.monotonic() - t
        print(f" ✗ ({elapsed:.1f}s) — {e}")
        logger.error("FAIL step: %s — %.1fs — %s", name, elapsed, e)
        raise
    else:
        elapsed = time.monotonic() - t
        print(f" ✓  ({elapsed:.1f}s)")
        logger.info("DONE step: %-40s %.1fs", name, elapsed)

# ─── 6. Model list ────────────────────────────────────────────────────────────
MODELS = [
    {
        "repo_id":  "bartowski/Qwen2.5-7B-Instruct-GGUF",
        "filename": "Qwen2.5-7B-Instruct-Q4_K_M.gguf",
        "desc":     "Qwen2.5-7B (Q4_K_M) — Text",
    },
    {
        "repo_id":  "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF",
        "filename": "qwen2.5-coder-7b-instruct-q4_k_m.gguf",
        "desc":     "Qwen2.5-coder-7B (Q4_K_M) — Coder",
    },
    {
        "repo_id":       "bartowski/Qwen_Qwen2.5-VL-7B-Instruct-GGUF",
        "filename":      "Qwen_Qwen2.5-VL-7B-Instruct-Q4_K_M.gguf",
        "desc":          "Qwen2.5-VL-7B (Q4_K_M) — Vision",
        "is_vision":     True,
        "mmproj_repo_id":  "bartowski/Qwen_Qwen2.5-VL-7B-Instruct-GGUF",
        "mmproj_filename": "mmproj-Qwen_Qwen2.5-VL-7B-Instruct-f16.gguf",
    },
]

FALLBACK_TEXT_MODEL = {
    "repo_id":  "bartowski/Qwen2.5-3B-Instruct-GGUF",
    "filename": "Qwen2.5-3B-Instruct-Q4_K_M.gguf",
    "desc":     "Qwen2.5-3B (Q4_K_M) — Text Fallback",
}

try:
    MODEL_IDX = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    if MODEL_IDX < 0 or MODEL_IDX >= len(MODELS):
        MODEL_IDX = 0
except (ValueError, IndexError):
    MODEL_IDX = 0

active_model_desc = MODELS[MODEL_IDX]["desc"]

# ─── 7. BM25 tokenizer hỗ trợ tiếng Việt ─────────────────────────────────────
# [FIX-6] CRITICAL: regex cũ r"[a-z0-9_]+" chỉ bắt Latin → bỏ sót TOÀN BỘ
# từ tiếng Việt trong BM25. Hệ quả: hybrid_retrieve = semantic search thuần,
# BM25 score luôn gần 0 với query/doc tiếng Việt → kết quả không sát nghĩa.
def _tokenize_vn(text: str) -> list:
    """
    Tokenizer BM25 hỗ trợ Việt + English + code.
    - Xử lý một số từ ghép tiếng Việt cơ bản
    - Loại bỏ stopwords cơ bản
    """
    t = text.lower()
    
    # Nối từ ghép cơ bản (có thể mở rộng thêm)
    compounds = {
        "tái tạo": "tái_tạo", "hình ảnh": "hình_ảnh", "mô hình": "mô_hình",
        "dữ liệu": "dữ_liệu", "hệ thống": "hệ_thống", "đầu vào": "đầu_vào",
        "đầu ra": "đầu_ra", "cấu hình": "cấu_hình", "giao diện": "giao_diện"
    }
    for k, v in compounds.items():
        t = t.replace(k, v)

    # Stopwords tiếng Việt cơ bản
    stopwords = {"là", "của", "và", "các", "trong", "được", "có", "cho", "với", "để", "những"}

    latin_tokens = re.findall(r"[a-z0-9][a-z0-9_]*", t)
    viet_tokens  = re.findall(r"[^\x00-\x7f\s.,!?;:()\[\]{}'\"<>/\\|@#$%^&*+=~`]+", t)
    
    tokens = latin_tokens + viet_tokens
    return [tk for tk in tokens if tk not in stopwords]


# ─── 8. Typed chunk ───────────────────────────────────────────────────────────
@dataclass
class ChunkResult:
    text:        str
    source_path: str
    loader_type: str
    is_image:    bool = False
    image_b64:   str | None = None
    metadata:    dict = field(default_factory=dict)

# ─── 8b. Vision helpers ──────────────────────────────────────────────────────
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"}

def _is_image_file(filepath: str) -> bool:
    return os.path.splitext(filepath)[1].lower() in _IMAGE_EXTS

def _image_to_data_uri(filepath: str, max_dim: int = 512) -> str:
    import io

    from PIL import Image
    ext = os.path.splitext(filepath)[1].lower()
    mime_map = {".jpg": "jpeg", ".jpeg": "jpeg", ".png": "png",
                ".bmp": "bmp", ".gif": "gif", ".webp": "webp"}
    mime = mime_map.get(ext, "jpeg")
    try:
        img = Image.open(filepath)
        w, h = img.size
        if max(w, h) > max_dim:
            ratio = max_dim / max(w, h)
            new_w = int(w * ratio)
            new_h = int(h * ratio)
            img = img.resize((new_w, new_h), Image.LANCZOS)
        if mime == "jpeg" and img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        buf = io.BytesIO()
        save_fmt = "JPEG" if mime == "jpeg" else mime.upper()
        img.save(buf, format=save_fmt, quality=85)
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    except Exception:
        with open(filepath, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
    return f"data:image/{mime};base64,{b64}"

def _load_image_for_embedding(filepath: str, max_dim: int = 256):
    from PIL import Image, ImageOps
    with Image.open(filepath) as img:
        img = ImageOps.exif_transpose(img).convert("RGB")
        img.thumbnail((max_dim, max_dim), Image.LANCZOS)
        return img.copy()

def _release_ml_memory():
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass

# ─── 9. Document Loaders ─────────────────────────────────────────────────────
class BaseDocumentLoader(ABC):
    # [FIX-9] Chunk nhỏ hơn: 1200 thay vì 1800
    MAX_CHUNK_CHARS = CHUNK_CHARS
    MIN_CHUNK_CHARS = 80
    OVERLAP_CHARS   = OVERLAP_CHARS

    _SENT_ENDINGS = (".\n", ". ", "!\n", "! ", "?\n", "? ", ";\n", "\n\n")

    @abstractmethod
    def can_handle(self, filepath: str) -> bool: ...

    @abstractmethod
    def load(self, filepath: str) -> list: ...

    def _is_quality_chunk(self, block: str) -> bool:
        stripped = block.strip()
        if len(stripped) < self.MIN_CHUNK_CHARS:
            return False
        non_comment = re.sub(
            r"^\s*(//[^\n]*|/\*.*?\*/)", "", stripped,
            flags=re.DOTALL | re.MULTILINE,
        ).strip()
        return len(non_comment) >= self.MIN_CHUNK_CHARS // 2

    def _snap_to_sentence(self, text: str) -> str:
        """Snap về ranh giới câu gần cuối nhất trong nửa sau của text."""
        min_pos = len(text) // 2
        best    = -1
        for ending in self._SENT_ENDINGS:
            pos = text.rfind(ending)
            if pos > min_pos and pos > best:
                best = pos
        return text[:best + 1] if best > min_pos else text

    def _sliding_window_chunks(self, content: str, filepath: str, label: str = "Source") -> list:
        """Sentence-aware sliding window chunking (v2.1+)."""
        rel     = _safe_relpath(filepath, PROJECT_DIR)
        results = []
        pos     = 0

        while pos < len(content):
            end   = pos + self.MAX_CHUNK_CHARS
            block = content[pos:end]

            if end < len(content) and len(block) > self.OVERLAP_CHARS * 2:
                snapped = self._snap_to_sentence(block)
                if len(snapped.strip()) >= self.MIN_CHUNK_CHARS:
                    block = snapped

            block = block.strip()
            if self._is_quality_chunk(block):
                results.append(ChunkResult(
                    text        = f"[{label}: {rel}]\n{block}",
                    source_path = filepath,
                    loader_type = label.lower().replace(" ", "_"),
                ))

            advance = max(len(block) - self.OVERLAP_CHARS,
                         self.MAX_CHUNK_CHARS - self.OVERLAP_CHARS)
            pos    += advance

        return results

    def _read_text_file(self, filepath: str) -> str | None:
        for enc in ("utf-8", "utf-16", "cp1252", "latin-1"):
            try:
                with open(filepath, "r", encoding=enc) as f:
                    return f.read()
            except (UnicodeDecodeError, ValueError):
                continue
        return None


class DocxLoader(BaseDocumentLoader):
    def can_handle(self, fp: str) -> bool: return fp.lower().endswith(".docx")
    def load(self, fp: str) -> list:
        from docx import Document
        doc  = Document(fp)
        text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        return self._sliding_window_chunks(text, fp, label="Tai lieu")


class PdfLoader(BaseDocumentLoader):
    def can_handle(self, fp: str) -> bool: return fp.lower().endswith(".pdf")
    def load(self, fp: str) -> list:
        text = self._extract_pdf_text(fp)
        if not text:
            raise ValueError(f"Không đọc được PDF: {fp}")
        return self._sliding_window_chunks(text, fp, label="Tai lieu PDF")

    def _extract_pdf_text(self, fp: str) -> str:
        try:
            import pdfplumber
            with pdfplumber.open(fp) as pdf:
                parts = [pg.extract_text(x_tolerance=2, y_tolerance=2) for pg in pdf.pages]
            text = "\n\n".join(p for p in parts if p).strip()
            if len(text) > 100:
                return text
        except Exception as e:
            logger.warning("pdfplumber failed %s: %s", fp, e)
        try:
            from pdfminer.high_level import extract_text
            text = extract_text(fp)
            if text and len(text.strip()) > 100:
                return text.strip()
        except Exception as e:
            logger.warning("pdfminer failed %s: %s", fp, e)
        return ""


class TxtLoader(BaseDocumentLoader):
    def can_handle(self, fp: str) -> bool: return fp.lower().endswith(".txt")
    def load(self, fp: str) -> list:
        content = self._read_text_file(fp)
        if not content or len(content.strip()) < self.MIN_CHUNK_CHARS:
            raise ValueError(f"File rỗng: {fp}")
        return self._sliding_window_chunks(content, fp, label="Tai lieu TXT")


class MarkdownLoader(BaseDocumentLoader):
    HEADING_RE = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)

    def can_handle(self, fp: str) -> bool: return fp.lower().endswith(".md")

    def load(self, fp: str) -> list:
        content = self._read_text_file(fp)
        if not content or len(content.strip()) < self.MIN_CHUNK_CHARS:
            raise ValueError(f"File rỗng: {fp}")

        rel     = _safe_relpath(fp, PROJECT_DIR)
        matches = list(self.HEADING_RE.finditer(content))
        if not matches:
            return self._sliding_window_chunks(content, fp, label="Source MD")

        results    = []
        boundaries = [m.start() for m in matches] + [len(content)]

        for i, match in enumerate(matches):
            level   = len(match.group(1))
            heading = match.group(2).strip()
            section = content[boundaries[i]:boundaries[i+1]].strip()
            section = self._strip_large_code_blocks(section)
            if not self._is_quality_chunk(section):
                continue

            prefix = f"[Source MD: {rel}] {'#'*level} {heading}\n"

            if len(section) <= self.MAX_CHUNK_CHARS:
                results.append(ChunkResult(
                    text        = prefix + section,
                    source_path = fp,
                    loader_type = "md",
                    metadata    = {"heading": heading, "level": level},
                ))
            else:
                for sc in self._sliding_window_chunks(section, fp, label="Source MD"):
                    sc.text     = prefix + sc.text.split("\n", 1)[-1]
                    sc.metadata = {"heading": heading, "level": level}
                    results.append(sc)

        return results

    def _strip_large_code_blocks(self, text: str) -> str:
        def maybe_strip(m):
            lines = m.group(0).count("\n")
            return m.group(0) if lines <= 30 else f"[code block omitted – {lines} lines]"
        return re.sub(r"```[\s\S]*?```", maybe_strip, text)


class CppHeaderLoader(BaseDocumentLoader):
    SOURCE_EXTS = {".cpp", ".h", ".py", ".cmake"}
    FUNC_RE     = re.compile(
        r"(?:^|\n)(?:"
        r"(?:class|struct|namespace)\s+\w+.*?\{"
        r"|(?:[\w:*&<>\[\]~]+\s+)+(?:\w+::)*\w+\s*\([^)]*\)\s*(?:const\s*)?(?:noexcept\s*)?\{"
        r")",
        re.MULTILINE,
    )

    def can_handle(self, fp: str) -> bool:
        return os.path.splitext(fp)[1].lower() in self.SOURCE_EXTS

    def load(self, fp: str) -> list:
        content = self._read_text_file(fp)
        if not content or len(content.strip()) < self.MIN_CHUNK_CHARS:
            raise ValueError(f"File rỗng: {fp}")
        ext = os.path.splitext(fp)[1].lower()
        return self._load_python(content, fp) if ext == ".py" else self._load_cpp(content, fp)

    def _load_cpp(self, content: str, fp: str) -> list:
        rel       = _safe_relpath(fp, PROJECT_DIR)
        positions = [m.start() for m in self.FUNC_RE.finditer(content)]
        results   = []
        if positions:
            positions.append(len(content))
            for i, start in enumerate(positions[:-1]):
                block = content[start:positions[i+1]].strip()
                if not self._is_quality_chunk(block):
                    continue
                header = block.split("\n")[0].strip().rstrip("{").strip()
                results.append(ChunkResult(
                    text        = f"[Source: {rel}] {header}\n{block[:self.MAX_CHUNK_CHARS]}",
                    source_path = fp,
                    loader_type = "source",
                    metadata    = {"symbol": header},
                ))
        else:
            results = self._sliding_window_chunks(content, fp, label="Source")
        return results

    def _load_python(self, content: str, fp: str) -> list:
        rel     = _safe_relpath(fp, PROJECT_DIR)
        results = []
        lines   = content.splitlines()
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return self._sliding_window_chunks(content, fp, label="Source")

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
                source_path = fp,
                loader_type = "source",
                metadata    = {"symbol": node.name, "scope": scope},
            ))

        return results or self._sliding_window_chunks(content, fp, label="Source")


class ImageLoader(BaseDocumentLoader):
    def can_handle(self, fp: str) -> bool:
        return _is_image_file(fp)

    def load(self, fp: str) -> list:
        try:
            rel = _safe_relpath(fp, PROJECT_DIR)
            return [ChunkResult(
                text=f"[Image: {rel}]",
                source_path=fp,
                loader_type="image",
                is_image=True,
            )]
        except Exception as e:
            logger.error("Error loading image %s: %s", fp, e)
            return []


# ─── 10. Loader Registry ──────────────────────────────────────────────────────
class DocumentLoaderRegistry:
    def __init__(self): self._loaders: list = []

    def register(self, loader) -> "DocumentLoaderRegistry":
        self._loaders.append(loader)
        return self

    def get_loader(self, fp: str) -> object | None:
        for loader in self._loaders:
            if loader.can_handle(fp):
                return loader
        return None

    def load_file(self, fp: str) -> list:
        loader = self.get_loader(fp)
        if loader is None:
            logger.debug("No loader for: %s", fp)
            return []
        try:
            return loader.load(fp)
        except Exception as e:
            logger.error("Error loading %s: %s", fp, e)
            return []


def build_registry() -> DocumentLoaderRegistry:
    return (
        DocumentLoaderRegistry()
        .register(DocxLoader())
        .register(PdfLoader())
        .register(TxtLoader())
        .register(MarkdownLoader())
        .register(CppHeaderLoader())
        .register(ImageLoader())
    )


# ─── 11. Document scanning ────────────────────────────────────────────────────
EXCLUDED_DIRS  = {
    ".git", "build", "__pycache__", ".qtcreator", ".cache", "Cache",
    "runs", "Dicom", "Predict", "3DModels", "Dataset", "logs",
    ".github", ".prompts", ".review", ".tasks", "scripts"
}
SCANNABLE_EXTS = {".cpp", ".h", ".py", ".md", ".cmake", ".jpg", ".jpeg", ".png", ".webp"}
DOC_EXTS_GLOB  = ("*.docx", "*.pdf", "*.txt", "*.jpg", "*.jpeg", "*.png", "*.webp")


def load_documents() -> list:
    registry   = build_registry()
    all_chunks: list = []
    stats = {"docx":0,"pdf":0,"txt":0,"md":0,"source":0,"errors":0,"files":0}

    if os.path.isdir(DOCS_DIR):
        for pattern in DOC_EXTS_GLOB:
            for fp in sorted(glob.glob(os.path.join(DOCS_DIR, pattern))):
                stats["files"] += 1
                results = registry.load_file(fp)
                if not results:
                    stats["errors"] += 1
                    continue
                for r in results:
                    all_chunks.append(r)
                    ext = os.path.splitext(fp)[1].lower().lstrip(".")
                    if ext in stats:
                        stats[ext] += 1

    for root, dirs, files in os.walk(PROJECT_DIR):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED_DIRS)
        for filename in sorted(files):
            if filename.startswith("~") or filename.endswith(".user"):
                continue
            ext = os.path.splitext(filename)[1].lower()
            if ext not in SCANNABLE_EXTS:
                continue
            fp = os.path.join(root, filename)
            stats["files"] += 1
            for r in registry.load_file(fp):
                all_chunks.append(r)
                stats["md" if r.loader_type == "md" else "source"] += 1

    logger.info(
        "Scanned: %d files → %d chunks (docx=%d pdf=%d txt=%d md=%d src=%d err=%d)",
        stats["files"], len(all_chunks),
        stats["docx"], stats["pdf"], stats["txt"],
        stats["md"], stats["source"], stats["errors"]
    )
    print(f"       files={stats['files']}  chunks={len(all_chunks)}"
          f"  (docx={stats['docx']} pdf={stats['pdf']} txt={stats['txt']}"
          f" md={stats['md']} src={stats['source']} err={stats['errors']})")
    return all_chunks


# ─── 12. Cache management ─────────────────────────────────────────────────────
def get_file_system_hash() -> str:
    entries = []
    if os.path.isdir(DOCS_DIR):
        for root, _, files in os.walk(DOCS_DIR):
            for f in sorted(files):
                path = os.path.join(root, f)
                try:
                    st = os.stat(path)
                    entries.append(f"{path}:{st.st_mtime:.3f}:{st.st_size}")
                except OSError:
                    pass

    for root, dirs, files in os.walk(PROJECT_DIR):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED_DIRS)
        for f in sorted(files):
            if os.path.splitext(f)[1].lower() not in SCANNABLE_EXTS:
                continue
            path = os.path.join(root, f)
            try:
                st = os.stat(path)
                entries.append(f"{path}:{st.st_mtime:.3f}:{st.st_size}")
            except OSError:
                pass

    # [v2.2] Thêm config vào hash: đổi EMBED_MODEL hoặc CHUNK_CHARS → tự rebuild
    entries.append(f"embed_model={EMBED_MODEL_NAME}")
    entries.append(f"chunk_chars={CHUNK_CHARS}")
    entries.append(f"cache_version={RAG_CACHE_VERSION}")

    combined = "\n".join(entries).encode("utf-8")
    return hashlib.md5(combined).hexdigest()


def is_cache_valid() -> bool:
    required = [CACHE_INDEX, CACHE_CHUNKS, CACHE_BM25, CACHE_METADATA]
    if not all(os.path.exists(p) for p in required):
        logger.debug("Cache miss: files missing")
        return False
    try:
        with open(CACHE_METADATA, "r", encoding="utf-8") as f:
            meta = json.load(f)
        current_hash = get_file_system_hash()
        valid = meta.get("fs_hash") == current_hash
        if not valid:
            logger.info("Cache stale (built_at=%s)", meta.get("built_at", "?"))
        else:
            logger.info("Cache valid: built_at=%s, chunks=%d",
                        meta.get("built_at", "?"), meta.get("chunk_count", 0))
        return valid
    except Exception as e:
        logger.warning("Cache read error: %s", e)
        return False


def save_cache(index, chunks: list, bm25) -> None:
    import faiss as _faiss
    t = time.monotonic()
    _faiss.write_index(index, CACHE_INDEX)
    with open(CACHE_CHUNKS, "wb") as f:
        pickle.dump(chunks, f, protocol=pickle.HIGHEST_PROTOCOL)
    with open(CACHE_BM25, "wb") as f:
        pickle.dump(bm25, f, protocol=pickle.HIGHEST_PROTOCOL)
    meta = {
        "fs_hash":     get_file_system_hash(),
        "chunk_count": len(chunks),
        "built_at":    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "model_idx":   MODEL_IDX,
        "embed_model": EMBED_MODEL_NAME,
        "chunk_chars": CHUNK_CHARS,
        "cache_version": RAG_CACHE_VERSION,
    }
    with open(CACHE_METADATA, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    total_mb = sum(os.path.getsize(p) for p in [CACHE_INDEX, CACHE_CHUNKS, CACHE_BM25]) / 1024**2
    logger.info("Cache saved: %.1fs | %.1fMB | %d chunks", time.monotonic()-t, total_mb, len(chunks))


def load_cache(embed_model):
    import faiss as _faiss
    t = time.monotonic()
    try:
        index = _faiss.read_index(CACHE_INDEX)
        with open(CACHE_CHUNKS, "rb") as f:
            chunks = pickle.load(f)
        with open(CACHE_BM25, "rb") as f:
            bm25 = pickle.load(f)
        logger.info("Cache loaded: %.1fs | chunks=%d", time.monotonic()-t, len(chunks))
        print(f"       chunks={len(chunks)}  (from Cache/)")
        return index, chunks, bm25
    except Exception as e:
        logger.error("Cache load failed: %s", e)
        return None, None, None


def build_index_from_scratch(chunks: list, embed_model):
    import faiss as _faiss
    import numpy as np
    from rank_bm25 import BM25Okapi

    t = time.monotonic()
    logger.info("Building index: %d chunks", len(chunks))

    t_enc = time.monotonic()
    text_indices = []
    texts = []
    image_items = []

    for i, c in enumerate(chunks):
        if getattr(c, "is_image", False):
            image_items.append((i, c.source_path))
        else:
            texts.append(getattr(c, "text", str(c)))
            text_indices.append(i)

    final_embeddings = [None] * len(chunks)
    
    if texts:
        print(f"Encoding {len(texts)} text chunks", end="", flush=True)
        text_embs = embed_model.encode(texts, show_progress_bar=False, normalize_embeddings=True, batch_size=64)
        for idx, emb in zip(text_indices, text_embs):
            final_embeddings[idx] = emb
            
    if image_items:
        image_batch_size = 4
        print(f"Encoding {len(image_items)} image chunks", end="", flush=True)
        for start in range(0, len(image_items), image_batch_size):
            batch_items = image_items[start:start + image_batch_size]
            batch_indices = []
            batch_images = []
            for idx, image_path in batch_items:
                try:
                    batch_images.append(_load_image_for_embedding(image_path))
                    batch_indices.append(idx)
                except Exception as e:
                    logger.warning("Image load skipped for embedding: %s | %s", image_path, e)

            if not batch_images:
                continue

            try:
                img_embs = embed_model.encode(
                    batch_images,
                    show_progress_bar=False,
                    normalize_embeddings=True,
                    batch_size=len(batch_images)
                )
                for idx, emb in zip(batch_indices, img_embs):
                    final_embeddings[idx] = emb
            except Exception as e:
                logger.warning(
                    "Image embedding batch failed (%d-%d): %s; retrying one by one",
                    start + 1, start + len(batch_items), e
                )
                for idx, img in zip(batch_indices, batch_images):
                    try:
                        emb = embed_model.encode(
                            [img],
                            show_progress_bar=False,
                            normalize_embeddings=True,
                            batch_size=1
                        )[0]
                        final_embeddings[idx] = emb
                    except Exception as single_e:
                        logger.warning(
                            "Image embedding skipped for %s: %s",
                            getattr(chunks[idx], "source_path", idx), single_e
                        )
            finally:
                for img in batch_images:
                    try:
                        img.close()
                    except Exception:
                        pass
                _release_ml_memory()

    missing_indices = [i for i, emb in enumerate(final_embeddings) if emb is None]
    if missing_indices:
        logger.warning("Falling back to text embeddings for %d chunks", len(missing_indices))
        fallback_texts = [getattr(chunks[i], "text", str(chunks[i])) for i in missing_indices]
        fallback_embs = embed_model.encode(
            fallback_texts,
            show_progress_bar=False,
            normalize_embeddings=True,
            batch_size=32
        )
        for idx, emb in zip(missing_indices, fallback_embs):
            final_embeddings[idx] = emb

    embeddings = final_embeddings
    print(f"\n ✓ Encoding complete ({time.monotonic()-t_enc:.1f}s)")

    emb  = np.array(embeddings, dtype="float32")
    dim, n = emb.shape[1], len(emb)

    if n < 1000:
        index = _faiss.IndexFlatIP(dim)
        _faiss.normalize_L2(emb)
        index.add(emb)
    else:
        nlist     = min(int(n**0.5), 256)
        quantizer = _faiss.IndexFlatIP(dim)
        index     = _faiss.IndexIVFFlat(quantizer, dim, nlist, _faiss.METRIC_INNER_PRODUCT)
        normed    = emb.copy()
        _faiss.normalize_L2(normed)
        index.train(normed)
        index.add(normed)

    logger.info("FAISS built: ntotal=%d dim=%d", index.ntotal, dim)

    # [FIX-6] Dùng _tokenize_vn thay vì r"[a-z0-9_]+" để BM25 xử lý được tiếng Việt
    tokenized = [_tokenize_vn(getattr(c, "text", str(c))) for c in chunks]
    bm25      = BM25Okapi(tokenized)

    save_cache(index, chunks, bm25)
    logger.info("Index built: %.1fs total", time.monotonic()-t)
    return index, chunks, bm25


def load_or_build_index(chunks: list, embed_model):
    if is_cache_valid():
        print("       [CACHE HIT]", end="")
        index, chunks_loaded, bm25 = load_cache(embed_model)
        if index is not None:
            return index, chunks_loaded, bm25
        print(" load failed, rebuilding...")
    print("       [CACHE MISS] Building index...")
    logger.info("Cache miss — rebuilding")
    return build_index_from_scratch(chunks, embed_model)


# ─── 13. RAG retrieval ────────────────────────────────────────────────────────
knowledge_index  = None
knowledge_chunks = []
bm25_index       = None
embed_model_ref  = None
_reranker        = None   # Cross-encoder, load lúc startup nếu USE_RERANKER=True
is_vision_model  = False  # True khi chạy Qwen2.5-VL (vision model)


def hybrid_retrieve(query: str, query_image_b64: str | None = None, k: int = 14, final_k: int = 10) -> list:
    """
    Hybrid semantic (text/image) + BM25 (text only) retrieval.
    """
    import numpy as np

    if knowledge_index is None or not knowledge_chunks:
        return []

    if embed_model_ref is None:
        if bm25_index is None or not query:
            return []
        bm25_raw = np.array(bm25_index.get_scores(_tokenize_vn(query)))
        bm25_top_idx = np.argsort(bm25_raw)[::-1][:final_k]
        return [knowledge_chunks[int(i)] for i in bm25_top_idx if i >= 0 and bm25_raw[int(i)] > 0]

    n = min(k, len(knowledge_chunks))

    # Semantic search with query text or query image
    if query_image_b64:
        import io

        from PIL import Image
        img_data = base64.b64decode(query_image_b64.split(",")[1])
        qv_input = [Image.open(io.BytesIO(img_data)).convert("RGB")]
    else:
        qv_input = [query]

    qv = embed_model_ref.encode(qv_input, normalize_embeddings=True)
    sem_raw, sem_idx = knowledge_index.search(np.array(qv, dtype="float32"), n)
    sem_raw = sem_raw[0]
    sem_idx = sem_idx[0]

    sem_min, sem_max = sem_raw.min(), sem_raw.max()
    sem_range = sem_max - sem_min if sem_max != sem_min else 1.0
    sem_norm  = {int(i): (s-sem_min)/sem_range for i, s in zip(sem_idx, sem_raw) if i >= 0}

    # [FIX-6] BM25 với Vietnamese tokenizer
    if query_image_b64:
        # BM25 is not used for image queries
        combined = [(cid, float(sem_raw[list(sem_idx).index(cid)])) for cid in sem_idx if cid >= 0]
    else:
        bm25_raw     = np.array(bm25_index.get_scores(_tokenize_vn(query)))
        bm25_top_idx = np.argsort(bm25_raw)[::-1][:n]
        b_scores     = bm25_raw[bm25_top_idx]
        b_min, b_max = b_scores.min(), b_scores.max()
        b_range      = b_max - b_min if b_max != b_min else 1.0
        bm25_norm    = {int(i): (s-b_min)/b_range for i, s in zip(bm25_top_idx, b_scores)}

        sem_idx_list = list(sem_idx)
        combined = []
        for cid in set(sem_norm) | set(bm25_norm):
            s = sem_norm.get(cid, 0.0)
            b = bm25_norm.get(cid, 0.0)
            raw_cos = float(sem_raw[sem_idx_list.index(cid)]) if cid in sem_norm else 0.0
            if raw_cos < SIMILARITY_THRESHOLD and b < 0.3:
                continue
            combined.append((cid, 0.55*s + 0.45*b))

    combined.sort(key=lambda x: x[1], reverse=True)
    return [knowledge_chunks[cid] for cid, _ in combined[:final_k]]


# [FIX-7] Cross-encoder re-ranking
def _rerank(query: str, chunks: list) -> list:
    if _reranker is None or not chunks:
        return chunks
    try:
        # Giới hạn độ dài để reranker nhanh hơn. Reranker chỉ chạy trên text.
        pairs  = [(query, getattr(c, "text", str(c))[:600]) for c in chunks]
        scores = _reranker.predict(pairs, show_progress_bar=False)
        ranked = sorted(zip(scores, chunks), key=lambda x: x[0], reverse=True)
        logger.debug("Rerank scores: %s", [f"{s:.3f}" for s, _ in ranked])
        return [c for _, c in ranked]
    except Exception as e:
        logger.warning("Reranker failed, fallback: %s", e)
        return chunks


# Source deduplication (v2.1)
def _dedup_by_source(chunks: list, max_per_source: int = 2) -> list:
    seen: dict = {}
    result: list = []
    for chunk in chunks:
        src = getattr(chunk, "source_path", str(chunk)[:80])
        if seen.get(src, 0) < max_per_source:
            result.append(chunk)
            seen[src] = seen.get(src, 0) + 1
    logger.debug("Dedup: %d → %d chunks (%d sources)", len(chunks), len(result), len(seen))
    return result


# [FIX-11] Context formatting với số thứ tự — model cite nguồn chính xác hơn
def _format_context_block(chunks: list, section_title: str) -> str:
    if not chunks:
        return ""
    lines = [f"=== {section_title} ==="]
    for i, chunk in enumerate(chunks, 1):
        src  = os.path.basename(getattr(chunk, "source_path", "nguồn"))
        text = getattr(chunk, "text", str(chunk))
        body = text.split("\n", 1)[-1].strip()  # Bỏ dòng prefix [...]
        lines.append(f"\n[{i}] {src}\n{body}")
    return "\n".join(lines)


def get_context(query: str, query_image_b64: str | None = None) -> tuple:
    if not ENABLE_RAG:
        return "", "", []

    # Bước 1: Hybrid retrieve
    candidates = hybrid_retrieve(query, query_image_b64=query_image_b64, k=14, final_k=10)

    # Bước 2: Cross-encoder re-rank
    if USE_RERANKER and query:
        candidates = _rerank(query, candidates)
        candidates = candidates[:RERANKER_TOP_K]

    # Bước 3: Source dedup
    candidates = _dedup_by_source(candidates, max_per_source=2)

    # Bước 4: Phân loại + cắt theo ngân sách context
    doc_chunks   = []
    code_chunks  = []
    image_chunks = []
    total        = 0

    for chunk in candidates:
        if getattr(chunk, "is_image", False):
            try:
                image_chunks.append(chunk.image_b64 or _image_to_data_uri(chunk.source_path))
            except Exception as e:
                logger.warning("Failed to prepare image context %s: %s",
                               getattr(chunk, "source_path", "?"), e)
            continue

        text_content = getattr(chunk, "text", str(chunk))
        remaining = MAX_CONTEXT_CHARS - total
        if remaining < 150:
            break
        trimmed_text = text_content[:remaining] if len(text_content) > remaining else text_content
        
        # Tạo bản sao chunk với text đã cắt gọn
        from copy import copy
        trimmed_chunk = copy(chunk) if hasattr(chunk, "text") else chunk
        if hasattr(trimmed_chunk, "text"):
            trimmed_chunk.text = trimmed_text

        if text_content.startswith("[Tai lieu"):
            doc_chunks.append(trimmed_chunk)
        else:
            code_chunks.append(trimmed_chunk)
        total += len(trimmed_text)

    doc_ctx  = _format_context_block(doc_chunks,  "TÀI LIỆU THAM KHẢO")
    code_ctx = _format_context_block(code_chunks, "MÃ NGUỒN LIÊN QUAN")
    return doc_ctx, code_ctx, image_chunks


# ─── 14. FastAPI + LLM ────────────────────────────────────────────────────────
llm = None
_chat_handler = None  # Qwen25VLChatHandler, chỉ dùng cho vision model


def estimate_tokens(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN)


def trim_history(messages: list, max_tokens: int = 2000) -> list:
    messages = list(messages)
    total    = sum(estimate_tokens(m.get("content", "")) for m in messages)
    while total > max_tokens and len(messages) > 1:
        removed = messages.pop(0)
        total  -= estimate_tokens(removed.get("content", ""))
    return messages


_CHARACTER_QUERY_PATTERNS = (
    r"\b(nhan\s*vat|thanh\s*vien|tac\s*gia|nguoi\s*tham\s*gia|nhan\s*su|doi\s*ngu|member|author|participant|character|person|people)\b",
    r"\b(team\s*lead|teamlead|leader|project\s*manager|dev\s*manager|devmanager|hr\s*manager|hrmanager|ky\s*su|engineer|developer)\b",
    r"\b(la\s+ai|who\s+is|who'?s|nguoi\s+nao|ai\s+phu\s+trach|ai\s+quan\s+ly)\b",
)
_ROLE_QUERY_PATTERN = r"\b(vai\s*tro|role)\b"
_PROJECT_CHARACTER_NAMES = (
    "john",
    "carpenter",
    "chris",
    "hoang",
    "nancy",
    "snow",
    "lavrov",
)


def _normalize_for_intent(text: str) -> str:
    text = (text or "").casefold().replace("đ", "d")
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", text).strip()


def _is_character_query(query: str) -> bool:
    """True khi câu hỏi đang hỏi về nhân vật/thành viên/vai trò trong dự án."""
    normalized = _normalize_for_intent(query)
    if not normalized:
        return False

    if any(re.search(pattern, normalized) for pattern in _CHARACTER_QUERY_PATTERNS):
        return True

    has_project_cue = re.search(r"\b(du\s*an|project|e2|e3|ewoosoft|story)\b", normalized)
    has_known_character = any(re.search(rf"\b{re.escape(name)}\b", normalized) for name in _PROJECT_CHARACTER_NAMES)
    if has_project_cue and has_known_character:
        return True

    return bool((has_project_cue or has_known_character) and re.search(_ROLE_QUERY_PATTERN, normalized))


def _strip_reference_citations_for_character_answer(answer: str) -> str:
    if not answer:
        return answer

    cleaned = re.sub(r"\s*\[(?:\d+)(?:\s*,\s*\d+)*\]", "", answer)
    source_exts = r"txt|md|pdf|docx?|pptx?|xlsx?|eml|html?"
    source_intro = rf"(?:Theo|Dựa trên)\s+(?:tài liệu|nguồn)\s*(?:tham khảo)?\s*(?:[^,\n]{{1,160}}\.(?:{source_exts})[,.:;]?\s*)?"
    cleaned = re.sub(rf"(?im)^\s*{source_intro}", "", cleaned)
    cleaned = re.sub(rf"(?i)(:\s*(?:\*\*)?\s*){source_intro}", r"\1", cleaned)
    cleaned = re.sub(r"(?im)^\s*(?:Tài liệu|Nguồn)\s*(?:tham khảo)?\s*[:\-–]\s*", "", cleaned)
    cleaned = re.sub(r"[ \t]+([,.;:])", r"\1", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def build_text_messages(messages: list, doc_ctx: str, code_ctx: str, suppress_citations: bool = False) -> list:
    system_prompt = _build_system_prompt(doc_ctx, code_ctx, suppress_citations=suppress_citations)
    result = [{"role": "system", "content": system_prompt}]
    
    history = trim_history(list(messages[:-1]), max_tokens=2000)
    for msg in history:
        role = "user" if msg.get("role") == "user" else "assistant"
        content = msg.get("content", "")
        if msg.get("attachments"):
            content += f"\n\n[Hệ thống: Người dùng tải lên: {', '.join(msg['attachments'])}. Model text-only, không thể xem ảnh.]"
        result.append({"role": role, "content": content})
        
    last = messages[-1]
    last_content = last.get("content", "")
    if last.get("attachments"):
        last_content += f"\n\n[Hệ thống: Người dùng tải lên: {', '.join(last['attachments'])}. Model text-only, không thể xem ảnh.]"
    result.append({"role": "user", "content": last_content})
    return result


def _build_system_prompt(doc_ctx: str, code_ctx: str, suppress_citations: bool = False) -> str:
    """Build system prompt chung cho cả text-only và vision model."""
    system_prompt = (
        "Bạn là trợ lý AI chuyên nghiệp cho dự án 3D-Reconstruction.\n"
        "NGUYÊN TẮC TRẢ LỜI:\n"
        "1. Dựa chủ yếu vào tài liệu và mã nguồn được cung cấp bên dưới để trả lời.\n"
        "2. Nếu câu hỏi không liên quan đến bất kỳ nội dung nào trong ngữ cảnh, "
        "   hãy nói ngắn gọn: 'Câu hỏi này nằm ngoài phạm vi tài liệu dự án.' rồi dừng.\n"
        "3. Không bịa đặt hoặc suy đoán thông tin kỹ thuật không có trong tài liệu.\n"
        "4. Khi nhắc đến code hoặc tài liệu, hãy ghi rõ số thứ tự nguồn [1], [2]... "
        "   tương ứng với danh sách ngữ cảnh bên dưới.\n"
        "5. Ưu tiên trả lời ĐẦY ĐỦ và CHI TIẾT — giải thích từng bước, nêu lý do "
        "   kỹ thuật, trích dẫn trực tiếp từ tài liệu khi có thể.\n"
        "6. Cấu trúc câu trả lời: tóm tắt ngắn → giải thích chi tiết → ví dụ/code.\n"
        "7. Trả lời bằng tiếng Việt trừ khi người dùng hỏi bằng tiếng Anh. Nếu tài liệu "
        "   nguồn là tiếng Anh, hãy DỊCH và GIẢI THÍCH sang tiếng Việt.\n"
        "8. LUÔN sử dụng định dạng Markdown (tiêu đề in đậm, bullet points, code blocks "
        "   có highlight syntax) để trình bày đẹp và dễ đọc.\n"
        "9. QUAN TRỌNG: Luôn hoàn thành câu cuối cùng trước khi kết thúc. "
        "   Không bao giờ dừng giữa câu, giữa đoạn code, hoặc giữa danh sách.\n"
        "10. Nếu người dùng gửi ảnh, hãy phân tích nội dung ảnh chi tiết "
        "    và liên hệ với tài liệu dự án nếu có thể.\n"
        "11. Nếu câu hỏi liên quan đến nhân vật trong dự án (như thành viên, tác giả, người tham gia), hãy trả lời trực tiếp mà KHÔNG trích dẫn tài liệu tham khảo.\n"
        "12. KHÔNG liệt kê hay in lại log 'TÀI LIỆU THAM KHẢO' hoặc 'MÃ NGUỒN LIÊN QUAN' trong câu trả lời.\n"
    )
    if suppress_citations:
        system_prompt += (
            "\nCHẾ ĐỘ CÂU HỎI NHÂN VẬT/VAI TRÒ ĐANG BẬT:\n"
            "- Câu hỏi hiện tại liên quan đến nhân vật, thành viên hoặc vai trò trong dự án.\n"
            "- Trả lời trực tiếp, tự nhiên; TUYỆT ĐỐI KHÔNG dùng ký hiệu nguồn như [1], [2].\n"
            "- KHÔNG viết các cụm mở đầu như 'Theo tài liệu', 'Theo nguồn', 'Tài liệu tham khảo'.\n"
            "- Vẫn dùng thông tin trong ngữ cảnh, nhưng không để lộ citation hoặc tên file nguồn trong câu trả lời.\n"
        )
    if doc_ctx:
        system_prompt += f"\n\n{doc_ctx}"
    if code_ctx:
        system_prompt += f"\n\n{code_ctx}"
    if not doc_ctx and not code_ctx:
        system_prompt += "\n\n[Không tìm thấy ngữ cảnh liên quan. Từ chối theo nguyên tắc số 2.]"
    return system_prompt


def build_vision_messages(messages: list, doc_ctx: str, code_ctx: str, image_chunks: list | None = None, suppress_citations: bool = False) -> list:
    """
    Build messages format cho create_chat_completion() — vision model.
    Ảnh đính kèm được encode thành base64 data URI theo OpenAI multimodal format.
    Chỉ ảnh ở message cuối cùng được gửi — ảnh cũ trong history bị bỏ qua
    để tiết kiệm context.
    """
    system_prompt = _build_system_prompt(doc_ctx, code_ctx, suppress_citations=suppress_citations)
    result = [{"role": "system", "content": system_prompt}]

    # History: chỉ gửi text, bỏ ảnh cũ
    history = trim_history(list(messages[:-1]), max_tokens=2000)
    for msg in history:
        role = "user" if msg.get("role") == "user" else "assistant"
        content = msg.get("content", "")
        result.append({"role": role, "content": content})

    # Message cuối: xử lý cả text + ảnh
    last = messages[-1]
    content_parts = []

    text_content = last.get("content", "")
    if text_content:
        content_parts.append({"type": "text", "text": text_content})

    # Encode ảnh đính kèm thành base64 data URI
    attachments = last.get("attachments") or []
    has_images = False
    for att in attachments:
        if os.path.isfile(att) and _is_image_file(att):
            try:
                data_uri = _image_to_data_uri(att)
                content_parts.append({
                    "type": "image_url",
                    "image_url": {"url": data_uri}
                })
                has_images = True
                logger.info("Vision: encoded image %s (%d bytes)",
                           os.path.basename(att), os.path.getsize(att))
            except Exception as e:
                logger.error("Failed to encode image %s: %s", att, e)
                content_parts.append(
                    {"type": "text", "text": f"[Lỗi đọc ảnh: {os.path.basename(att)}]"}
                )
        else:
            content_parts.append(
                {"type": "text", "text": f"[File đính kèm: {os.path.basename(att)}]"}
            )

    if image_chunks:
        for b64 in image_chunks:
            content_parts.append(
                {"type": "image_url", "image_url": {"url": b64}}
            )
            has_images = True
            logger.info("Vision: added retrieved image chunk to context")

    # Nếu không có ảnh, thêm hint cho model
    if attachments and not has_images:
        content_parts.append(
            {"type": "text", "text": "[Không có ảnh hợp lệ trong file đính kèm.]"}
        )

    if not content_parts:
        content_parts.append({"type": "text", "text": "(trống)"})

    result.append({"role": "user", "content": content_parts})
    logger.debug("Vision messages: %d parts (has_images=%s)", len(content_parts), has_images)
    return result


# ── Pydantic models ────────────────────────────────────────────────────────────
class ChatMessage(BaseModel):
    role:        str
    content:     str = Field(..., max_length=32000)
    attachments: list[str] | None = None

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str) -> str:
        if v not in ("user", "assistant", "system"):
            raise ValueError("role phải là user/assistant/system")
        return v

class ChatRequest(BaseModel):
    messages:    list[ChatMessage] = Field(..., min_length=1)
    temperature: float = Field(0.7, ge=0.0, le=2.0)
    max_tokens:  int   = Field(2048, ge=1, le=4096)


# ── FastAPI app ────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    total = time.monotonic() - _SERVER_START_TIME
    logger.info("Server ready in %.1fs — http://127.0.0.1:8080", total)
    print(f"\n{'═'*54}")
    print(f"  ✅  Server ready in {total:.1f}s total")
    print("  🌐  http://127.0.0.1:8080")
    print(f"  📋  Log: {_safe_relpath(LOG_FILE_PATH, BASE_DIR)}")
    print(f"  🔍  Reranker: {'ON' if _reranker else 'OFF'}")
    print(f"  👁️  Vision: {'YES' if is_vision_model else 'no'}")
    print(f"{'═'*54}\n")
    print("[SUCCESS] AI Server started successfully")
    sys.stdout.flush()
    yield
    logger.info("Server shutdown after %.1fs", time.monotonic() - _SERVER_START_TIME)
    print("\n👋 Server stopped.")


app = FastAPI(title="3D-Reconstruction AI Server", version="2.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["POST","GET"], allow_headers=["*"]
)


llm_lock = threading.Lock()
current_task_id = None

@app.post("/v1/chat/completions")
def chat_completions(request: ChatRequest, http_req: Request):
    if llm is None:
        raise HTTPException(status_code=503, detail="LLM chưa khởi tạo")

    req_start  = time.monotonic()
    user_query = request.messages[-1].content
    attachments = request.messages[-1].attachments or []
    query_image_b64 = None
    for att in attachments:
        if _is_image_file(att):
            try:
                query_image_b64 = _image_to_data_uri(att)
                break
            except Exception as e:
                logger.error("Failed to read attachment for retrieval: %s", e)

    logger.info("[MODE: CHAT] Query from %s: %s…", http_req.client.host,
                user_query[:60].replace("\n", " "))
    suppress_citations = _is_character_query(user_query)

    t_rag             = time.monotonic()
    doc_ctx, code_ctx, image_chunks = get_context(user_query, query_image_b64=query_image_b64)
    if not query_image_b64:
        image_chunks = []
    rag_ms            = (time.monotonic() - t_rag) * 1000

    messages_raw     = [m.model_dump() for m in request.messages]

    if is_vision_model:
        msgs = build_vision_messages(messages_raw, doc_ctx, code_ctx, image_chunks, suppress_citations=suppress_citations)
        estimated_tokens = sum(estimate_tokens(m.get("text", "")) for p in msgs for m in (p.get("content") if isinstance(p.get("content"), list) else [{"text": p.get("content", "")}]))
    else:
        msgs = build_text_messages(messages_raw, doc_ctx, code_ctx, suppress_citations=suppress_citations)
        estimated_tokens = sum(estimate_tokens(m.get("content", "")) for m in msgs)

    if estimated_tokens >= LLM_N_CTX - 512:
        raise HTTPException(
            status_code=400,
            detail=f"Hội thoại quá dài (~{estimated_tokens} tokens). Vui lòng bắt đầu phiên mới."
        )

    available_tokens = LLM_N_CTX - estimated_tokens - 400
    max_tokens       = min(request.max_tokens, max(512, available_tokens))
    logger.info(
        "Context ready | tokens≈%d/%d max_tokens=%d vision=%s images=%d character_query=%s",
        estimated_tokens, LLM_N_CTX, max_tokens, is_vision_model, len(image_chunks), suppress_citations
    )

    def run_llm():
        with llm_lock:
            answer = ""
            finish_reason = "stop"
            start_time = time.monotonic()
            
            try:
                logger.info("LLM inference start")
                response_iter = llm.create_chat_completion(
                    messages       = msgs,
                    max_tokens     = max_tokens,
                    temperature    = request.temperature,
                    repeat_penalty = 1.1,
                    stream         = True,
                )
                
                for chunk in response_iter:
                    delta = chunk["choices"][0].get("delta", {})
                    if "content" in delta:
                        answer += delta["content"]
                        
                    fr = chunk["choices"][0].get("finish_reason")
                    if fr is not None:
                        finish_reason = fr

            except Exception as e:
                logger.error("LLM error: %s", e)
                raise
                
            return answer.strip(), finish_reason, (time.monotonic() - start_time) * 1000

    try:
        answer, finish_reason, llm_ms = run_llm()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi inference: {e}")

    if suppress_citations:
        answer = _strip_reference_citations_for_character_answer(answer)

    total_ms = (time.monotonic() - req_start) * 1000

    if finish_reason == "length":
        logger.warning(
            "Answer truncated (finish_reason=length): max_tokens=%d, estimated_prompt=%d",
            max_tokens, estimated_tokens
        )
        answer += "\n\n⚠️ *(Câu trả lời có thể chưa hoàn chỉnh — ngữ cảnh quá dài. "
        answer += "Hãy hỏi cụ thể hơn hoặc bắt đầu hội thoại mới.)*"

    logger.info(
        "Done | rag=%.0fms llm=%.0fms total=%.0fms | "
        "ctx=%d+%d ans=%d tokens≈%d/%d finish=%s vision=%s",
        rag_ms, llm_ms, total_ms,
        len(doc_ctx), len(code_ctx), len(answer),
        estimated_tokens, LLM_N_CTX, finish_reason, is_vision_model,
    )

    return {
        "id":      f"chatcmpl-{int(req_start*1000)}",
        "object":  "chat.completion",
        "choices": [{"message": {"role": "assistant", "content": answer},
                     "finish_reason": finish_reason}],
        "usage":   {},
        "x_meta":  {
            "rag_ms":           round(rag_ms),
            "llm_ms":           round(llm_ms),
            "total_ms":         round(total_ms),
            "estimated_tokens": estimated_tokens,
            "max_tokens_used":  max_tokens,
            "finish_reason":    finish_reason,
            "reranker_active":  _reranker is not None,
            "vision_model":     is_vision_model,
            "character_query":  suppress_citations,
        },
    }


@app.get("/health")
async def health():
    return {
        "status":          "ok",
        "uptime_sec":      round(time.monotonic() - _SERVER_START_TIME, 1),
        "llm_loaded":      llm is not None,
        "rag_chunks":      len(knowledge_chunks),
        "reranker":        _reranker is not None,
        "embed_model":     EMBED_MODEL_NAME,
        "chunk_chars":     CHUNK_CHARS,
        "chars_per_token": CHARS_PER_TOKEN,
        "max_context":     MAX_CONTEXT_CHARS,
        "model":           active_model_desc,
        "is_vision":       is_vision_model,
    }


@app.get("/v1/models")
async def list_models():
    return {"data": [{"id": active_model_desc,
                      "object": "model",
                      "desc": MODELS[MODEL_IDX]["desc"]}]}


# ─── 14b. Agent Tools & Execution ─────────────────────────────────────────────
# [v2.3] AI Agent: Tool-calling loop cho phép LLM tự thực thi các task trên project

import fnmatch
import subprocess

# ── Tool Definitions (mô tả cho LLM) ──────────────────────────────────────────
AGENT_TOOLS = [
    {
        "name": "read_file",
        "description": "Read the content of a file. Returns file content as text. "
                       "Use this to examine source code, configs, documentation, etc.",
        "parameters": {
            "path": {"type": "string", "description": "Relative path from project root (e.g. 'src/main.cpp')", "required": True},
            "start_line": {"type": "integer", "description": "Start line (1-indexed, inclusive). Omit to read from beginning.", "required": False},
            "end_line": {"type": "integer", "description": "End line (1-indexed, inclusive). Omit to read to end.", "required": False},
        },
    },
    {
        "name": "list_directory",
        "description": "List files and subdirectories in a directory. "
                       "Returns a structured listing with file sizes and types.",
        "parameters": {
            "path": {"type": "string", "description": "Relative path from project root (e.g. 'src/modules'). Use '.' for project root.", "required": True},
            "recursive": {"type": "boolean", "description": "If true, list recursively. Default false.", "required": False},
            "max_depth": {"type": "integer", "description": "Max depth for recursive listing. Default 3.", "required": False},
        },
    },
    {
        "name": "search_text",
        "description": "Search for text/pattern in project files. Returns matching lines with file paths and line numbers. "
                       "Similar to grep. Use this to find usages, definitions, or occurrences of text.",
        "parameters": {
            "query": {"type": "string", "description": "Text or pattern to search for", "required": True},
            "path": {"type": "string", "description": "Relative path to search in. Default: entire project.", "required": False},
            "file_pattern": {"type": "string", "description": "Glob pattern to filter files, e.g. '*.cpp' or '*.py'", "required": False},
            "case_sensitive": {"type": "boolean", "description": "Case sensitive search. Default true.", "required": False},
            "max_results": {"type": "integer", "description": "Max results to return. Default 50.", "required": False},
        },
    },
    {
        "name": "analyze_code",
        "description": "Analyze the structure of a source code file. Returns classes, functions, imports, "
                       "and a structural summary. Supports Python and C/C++ files.",
        "parameters": {
            "path": {"type": "string", "description": "Relative path to the source file", "required": True},
        },
    },
    {
        "name": "write_file",
        "description": "Write content to a file. THIS REQUIRES USER APPROVAL before execution. "
                       "Use this to fix bugs, add code, modify configs, etc.",
        "parameters": {
            "path": {"type": "string", "description": "Relative path from project root", "required": True},
            "content": {"type": "string", "description": "Full content to write to the file", "required": True},
            "description": {"type": "string", "description": "Brief description of what this change does", "required": True},
        },
    },
    {
        "name": "run_command",
        "description": "Execute a shell command. THIS REQUIRES USER APPROVAL before execution. "
                       "Use for building, testing, or other project tasks.",
        "parameters": {
            "command": {"type": "string", "description": "The shell command to run", "required": True},
            "cwd": {"type": "string", "description": "Working directory (relative to project root). Default: project root.", "required": False},
            "timeout": {"type": "integer", "description": "Timeout in seconds. Default 30.", "required": False},
        },
    },
    {
        "name": "get_project_status",
        "description": "Inspect the project safely: current Git branch, changed files and high-level source file counts. Does not change files.",
        "parameters": {},
    },
    {
        "name": "validate_file",
        "description": "Validate a Python or JSON file without modifying it. Python files are syntax-checked; JSON files are parsed.",
        "parameters": {
            "path": {"type": "string", "description": "Relative Python or JSON file path", "required": True},
        },
    },
    {
        "name": "patch_file",
        "description": "Replace an exact text fragment in an existing file. THIS REQUIRES USER APPROVAL. Read the file first and use a unique fragment.",
        "parameters": {
            "path": {"type": "string", "description": "Relative path from project root", "required": True},
            "find": {"type": "string", "description": "Exact existing text to replace", "required": True},
            "replace": {"type": "string", "description": "Replacement text", "required": True},
            "description": {"type": "string", "description": "Brief description of the change", "required": True},
        },
    },
    {
        "name": "create_directory",
        "description": "Create a directory inside the project. THIS REQUIRES USER APPROVAL.",
        "parameters": {
            "path": {"type": "string", "description": "Relative directory path", "required": True},
            "description": {"type": "string", "description": "Why this directory is needed", "required": True},
        },
    },
    {
        "name": "application_action",
        "description": "Run a 3D-Reconstruction desktop UI action. Use this instead of source-code tools when the user asks to operate the application. "
                       "Supported actions: viewer.load_2d, viewer.load_3d, viewer.load_dicom, reconstruction.load_images, reconstruction.start_reconstruction, reconstruction.view_3d_model, reconstruction.close_3d_model, ai.run_detection, ai.run_segmentation, ai.video_tracking, ai.hide_results, ai.training_model, ai.view_training_charts, assistant.open, assistant.close, mail.open, mail.close, mail.settings, help.about, language.change, admin.settings, admin.change_avatar, admin.change_password, admin.logout. "
                       "Use language.change with language='vi' or language='en'. The desktop client performs the action using its configured sample paths.",
        "parameters": {
            "action": {"type": "string", "description": "One supported desktop action name", "required": True},
            "language": {"type": "string", "description": "Only for language.change: vi or en", "required": False},
        },
    },
]

# ── Safety: các thư mục/file cấm truy cập ────────────────────────────────────
_AGENT_BLOCKED_DIRS = {".git", "build", "__pycache__", ".vs", "node_modules"}
_AGENT_BLOCKED_EXTS = {".exe", ".dll", ".so", ".bin", ".dat", ".pkl", ".gguf", ".onnx", ".pt"}
_AGENT_MAX_FILE_READ_CHARS = 50000  # ~25K tokens
_AGENT_MAX_ITERATIONS = 12


def _agent_safe_path(rel_path: str) -> str | None:
    """Validate and resolve a relative path within PROJECT_DIR. Returns None if unsafe."""
    if not rel_path:
        return None
    # Normalize separators
    rel_path = rel_path.replace("\\", "/").strip("/")
    # Block traversal
    if ".." in rel_path.split("/"):
        return None
    abs_path = os.path.normpath(os.path.join(PROJECT_DIR, rel_path))
    # Ensure within project
    if not abs_path.startswith(os.path.normpath(PROJECT_DIR)):
        return None
    # Check blocked dirs
    parts = rel_path.split("/")
    for part in parts:
        if part in _AGENT_BLOCKED_DIRS:
            return None
    return abs_path


# ── Tool Executor Functions ───────────────────────────────────────────────────

def tool_read_file(params: dict) -> dict:
    """Read file content with optional line range."""
    path = params.get("path", "")
    abs_path = _agent_safe_path(path)
    if abs_path is None:
        return {"error": f"Đường dẫn không hợp lệ hoặc bị chặn: {path}"}
    if not os.path.isfile(abs_path):
        return {"error": f"File không tồn tại: {path}"}
    ext = os.path.splitext(abs_path)[1].lower()
    if ext in _AGENT_BLOCKED_EXTS:
        return {"error": f"Không thể đọc file binary: {path}"}

    try:
        for enc in ("utf-8", "utf-16", "cp1252", "latin-1"):
            try:
                with open(abs_path, "r", encoding=enc) as f:
                    lines = f.readlines()
                break
            except (UnicodeDecodeError, ValueError):
                continue
        else:
            return {"error": f"Không đọc được encoding của file: {path}"}

        total_lines = len(lines)
        start = max(1, params.get("start_line", 1)) - 1  # 0-indexed
        end = min(total_lines, params.get("end_line", total_lines))

        selected = lines[start:end]
        content = "".join(selected)

        if len(content) > _AGENT_MAX_FILE_READ_CHARS:
            content = content[:_AGENT_MAX_FILE_READ_CHARS] + f"\n... [truncated at {_AGENT_MAX_FILE_READ_CHARS} chars]"

        return {
            "path": path,
            "total_lines": total_lines,
            "showing": f"lines {start+1}-{end}",
            "content": content,
        }
    except Exception as e:
        return {"error": f"Lỗi đọc file {path}: {e}"}


def tool_list_directory(params: dict) -> dict:
    """List directory contents."""
    path = params.get("path", ".")
    if path == ".":
        abs_path = PROJECT_DIR
    else:
        abs_path = _agent_safe_path(path)
    if abs_path is None:
        return {"error": f"Đường dẫn không hợp lệ: {path}"}
    if not os.path.isdir(abs_path):
        return {"error": f"Thư mục không tồn tại: {path}"}

    recursive = params.get("recursive", False)
    max_depth = params.get("max_depth", 3)
    entries = []
    count = 0
    max_entries = 500

    try:
        if recursive:
            for root, dirs, files in os.walk(abs_path):
                dirs[:] = sorted(d for d in dirs if d not in _AGENT_BLOCKED_DIRS)
                depth = root.replace(abs_path, "").count(os.sep)
                if depth >= max_depth:
                    dirs.clear()
                    continue
                rel_root = os.path.relpath(root, PROJECT_DIR)
                for d in sorted(dirs):
                    if count >= max_entries:
                        break
                    entries.append({"name": os.path.join(rel_root, d), "type": "directory"})
                    count += 1
                for f in sorted(files):
                    if count >= max_entries:
                        break
                    fp = os.path.join(root, f)
                    try:
                        size = os.path.getsize(fp)
                    except OSError:
                        size = 0
                    entries.append({
                        "name": os.path.join(rel_root, f),
                        "type": "file",
                        "size_bytes": size,
                    })
                    count += 1
                if count >= max_entries:
                    break
        else:
            for item in sorted(os.listdir(abs_path)):
                if item in _AGENT_BLOCKED_DIRS:
                    continue
                if count >= max_entries:
                    break
                fp = os.path.join(abs_path, item)
                rel = os.path.relpath(fp, PROJECT_DIR)
                if os.path.isdir(fp):
                    entries.append({"name": rel, "type": "directory"})
                else:
                    try:
                        size = os.path.getsize(fp)
                    except OSError:
                        size = 0
                    entries.append({"name": rel, "type": "file", "size_bytes": size})
                count += 1

        return {"path": path, "count": len(entries), "entries": entries}
    except Exception as e:
        return {"error": f"Lỗi liệt kê thư mục {path}: {e}"}


def tool_search_text(params: dict) -> dict:
    """Search for text in project files."""
    query = params.get("query", "")
    if not query:
        return {"error": "Query rỗng"}

    search_path = params.get("path", ".")
    if search_path == ".":
        abs_search = PROJECT_DIR
    else:
        abs_search = _agent_safe_path(search_path)
    if abs_search is None:
        return {"error": f"Đường dẫn không hợp lệ: {search_path}"}

    file_pattern = params.get("file_pattern", "*")
    case_sensitive = params.get("case_sensitive", True)
    max_results = min(params.get("max_results", 50), 100)

    results = []
    search_query = query if case_sensitive else query.lower()
    text_exts = {".cpp", ".h", ".py", ".md", ".txt", ".cmake", ".json", ".xml", ".html", ".css", ".js", ".ts", ".yaml", ".yml", ".toml", ".cfg", ".ini", ".bat", ".sh"}

    try:
        for root, dirs, files in os.walk(abs_search):
            dirs[:] = sorted(d for d in dirs if d not in _AGENT_BLOCKED_DIRS)
            for filename in sorted(files):
                ext = os.path.splitext(filename)[1].lower()
                if ext not in text_exts:
                    continue
                if file_pattern != "*" and not fnmatch.fnmatch(filename, file_pattern):
                    continue

                fp = os.path.join(root, filename)
                rel = os.path.relpath(fp, PROJECT_DIR)
                try:
                    with open(fp, "r", encoding="utf-8", errors="replace") as f:
                        for line_no, line in enumerate(f, 1):
                            check_line = line if case_sensitive else line.lower()
                            if search_query in check_line:
                                results.append({
                                    "file": rel,
                                    "line": line_no,
                                    "content": line.rstrip()[:200],
                                })
                                if len(results) >= max_results:
                                    return {"query": query, "count": len(results), "truncated": True, "results": results}
                except (OSError, UnicodeDecodeError):
                    continue

        return {"query": query, "count": len(results), "truncated": False, "results": results}
    except Exception as e:
        return {"error": f"Lỗi tìm kiếm: {e}"}


def tool_analyze_code(params: dict) -> dict:
    """Analyze code structure of a file."""
    path = params.get("path", "")
    abs_path = _agent_safe_path(path)
    if abs_path is None:
        return {"error": f"Đường dẫn không hợp lệ: {path}"}
    if not os.path.isfile(abs_path):
        return {"error": f"File không tồn tại: {path}"}

    ext = os.path.splitext(abs_path)[1].lower()
    try:
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception as e:
        return {"error": f"Lỗi đọc file: {e}"}

    total_lines = content.count("\n") + 1
    result = {
        "path": path,
        "extension": ext,
        "total_lines": total_lines,
        "size_bytes": len(content.encode("utf-8")),
    }

    if ext == ".py":
        return _analyze_python(content, result)
    elif ext in (".cpp", ".h", ".c", ".hpp"):
        return _analyze_cpp(content, result)
    else:
        # Generic analysis
        result["analysis"] = "File type không hỗ trợ phân tích chi tiết. Dùng read_file để xem nội dung."
        return result


def _analyze_python(content: str, result: dict) -> dict:
    """Python AST-based analysis."""
    try:
        tree = ast.parse(content)
    except SyntaxError as e:
        result["syntax_error"] = str(e)
        return result

    classes = []
    functions = []
    imports = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            methods = [n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            bases = [ast.dump(b) if not hasattr(b, "id") else b.id for b in node.bases]
            classes.append({
                "name": node.name,
                "line": node.lineno,
                "methods": methods,
                "bases": bases,
            })
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # Only top-level functions (not methods)
            if not any(isinstance(p, ast.ClassDef) for p in ast.walk(tree)):
                args = [a.arg for a in node.args.args]
                functions.append({
                    "name": node.name,
                    "line": node.lineno,
                    "args": args,
                    "is_async": isinstance(node, ast.AsyncFunctionDef),
                })
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            else:
                module = node.module or ""
                for alias in node.names:
                    imports.append(f"{module}.{alias.name}")

    # Re-parse for top-level functions only
    top_functions = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = [a.arg for a in node.args.args]
            docstring = ast.get_docstring(node)
            top_functions.append({
                "name": node.name,
                "line": node.lineno,
                "end_line": getattr(node, "end_lineno", node.lineno),
                "args": args,
                "is_async": isinstance(node, ast.AsyncFunctionDef),
                "docstring": (docstring[:100] + "...") if docstring and len(docstring) > 100 else docstring,
            })

    result["classes"] = classes
    result["functions"] = top_functions
    result["imports"] = imports[:30]  # Limit
    return result


def _analyze_cpp(content: str, result: dict) -> dict:
    """Regex-based C++ analysis."""
    classes = []
    functions = []
    includes = []

    # Find #include
    for m in re.finditer(r'^#include\s+[<"]([^>"]+)[>"]', content, re.MULTILINE):
        includes.append(m.group(1))

    # Find class/struct declarations
    for m in re.finditer(r'^(?:class|struct)\s+(?:\w+\s+)?(\w+)\s*(?::\s*(?:public|private|protected)\s+(\w+))?\s*\{',
                          content, re.MULTILINE):
        classes.append({
            "name": m.group(1),
            "base": m.group(2),
            "line": content[:m.start()].count("\n") + 1,
        })

    # Find function definitions (simplified)
    func_re = re.compile(
        r'^(?:[\w:*&<>\[\]~]+\s+)+(?:(\w+)::)?(\w+)\s*\([^)]*\)\s*(?:const\s*)?(?:override\s*)?(?:noexcept\s*)?(?:\{|;)',
        re.MULTILINE,
    )
    for m in func_re.finditer(content):
        scope = m.group(1) or ""
        name = m.group(2)
        if name in ("if", "for", "while", "switch", "return", "catch"):
            continue
        functions.append({
            "name": f"{scope}::{name}" if scope else name,
            "line": content[:m.start()].count("\n") + 1,
        })

    result["classes"] = classes[:50]
    result["functions"] = functions[:100]
    result["includes"] = includes[:30]
    return result


# ── Tool dispatch ──────────────────────────────────────────────────────────────

def tool_get_project_status(params: dict) -> dict:
    """Return a lightweight, read-only project status."""
    source_counts = {}
    for root, dirs, files in os.walk(PROJECT_DIR):
        dirs[:] = [d for d in dirs if d not in _AGENT_BLOCKED_DIRS]
        for filename in files:
            ext = os.path.splitext(filename)[1].lower()
            if ext in {".cpp", ".h", ".py", ".json", ".cmake"}:
                source_counts[ext] = source_counts.get(ext, 0) + 1
    try:
        branch = subprocess.run(["git", "branch", "--show-current"], cwd=PROJECT_DIR,
                                capture_output=True, text=True, timeout=5, check=False).stdout.strip()
        changed = subprocess.run(["git", "status", "--short"], cwd=PROJECT_DIR,
                                 capture_output=True, text=True, timeout=5, check=False).stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        branch, changed = "", []
    return {"project_root": PROJECT_DIR, "git_branch": branch, "changed_files": changed[:100],
            "source_file_counts": source_counts}


def tool_validate_file(params: dict) -> dict:
    """Validate Python or JSON syntax without executing project code."""
    path = params.get("path", "")
    abs_path = _agent_safe_path(path)
    if abs_path is None or not os.path.isfile(abs_path):
        return {"error": f"Invalid or missing file: {path}"}
    ext = os.path.splitext(abs_path)[1].lower()
    try:
        with open(abs_path, "r", encoding="utf-8") as handle:
            content = handle.read()
        if ext == ".py":
            compile(content, path, "exec")
        elif ext == ".json":
            json.loads(content)
        else:
            return {"error": "Only .py and .json files can be validated."}
        return {"success": True, "path": path, "validation": "syntax_valid"}
    except (SyntaxError, ValueError) as error:
        return {"success": False, "path": path, "error": str(error)}


_DESKTOP_ACTIONS = {
    "viewer.load_2d", "viewer.load_3d", "viewer.load_dicom",
    "reconstruction.load_images", "reconstruction.start_reconstruction",
    "reconstruction.view_3d_model", "reconstruction.close_3d_model",
    "ai.run_detection", "ai.run_segmentation", "ai.video_tracking",
    "ai.hide_results", "ai.training_model", "ai.view_training_charts",
    "assistant.open", "assistant.close", "mail.open", "mail.close", "mail.settings",
    "help.about", "language.change", "admin.settings", "admin.change_avatar",
    "admin.change_password", "admin.logout",
}

# Small local models occasionally invent a close-but-invalid action name.  Keep
# the server-to-Qt contract canonical instead of letting such an alias trigger
# unnecessary source-code searches and consume the agent context window.
_DESKTOP_ACTION_ALIASES = {
    "reconstruction.load_3d_model": "viewer.load_3d",
    "viewer.load_3d_model": "viewer.load_3d",
    "reconstruction.load_2d_image": "viewer.load_2d",
    "viewer.load_2d_image": "viewer.load_2d",
    "viewer.load_dicom_series": "viewer.load_dicom",
    "reconstruction.load_dicom_series": "viewer.load_dicom",
    "reconstruction.run": "reconstruction.start_reconstruction",
    "reconstruction.start": "reconstruction.start_reconstruction",
    "reconstruction.toggle_3d_model": "reconstruction.view_3d_model",
    "ai.detection": "ai.run_detection",
    "ai.segmentation": "ai.run_segmentation",
    "ai.tracking": "ai.video_tracking",
    "language.set": "language.change",
    "language.changed": "language.change",
    "mail.inbox": "mail.open",
    "mail.open_inbox": "mail.open",
}


def _canonical_desktop_action(params: dict) -> dict | None:
    """Return canonical action parameters, or None for an unknown action."""
    canonical = _DESKTOP_ACTION_ALIASES.get(params.get("action", ""), params.get("action", ""))
    if canonical not in _DESKTOP_ACTIONS:
        return None
    result = dict(params)
    result["action"] = canonical
    return result


def _normalise_agent_task(text: str) -> str:
    normalized = unicodedata.normalize("NFD", text.casefold())
    normalized = "".join(c for c in normalized if unicodedata.category(c) != "Mn")
    normalized = normalized.replace("đ", "d")
    # Vietnamese đ is not decomposed by NFD, so normalize it explicitly.
    normalized = normalized.replace("đ", "d")
    return re.sub(r"\s+", " ", normalized).strip()


# Từ khóa gợi ý đây là lệnh điều khiển UI — dùng để (a) tắt bơm RAG context
# không liên quan khi request rơi xuống LangGraph, và (b) làm tín hiệu cho
# bước self-correction trong _run_langgraph_agent khi model bỏ lỡ tool_call
# ở lượt suy luận đầu tiên. Đây KHÔNG phải một fast-path bypass — quyết định
# gọi tool cuối cùng vẫn hoàn toàn do model đưa ra qua LangGraph.
_UI_ACTION_HINT_WORDS = (
    "ngon ngu", "doi ngon ngu", "sang tieng viet", "sang tieng anh",
    "sang english", "language", "mail", "avatar", "mat khau", "password",
    "dang xuat", "logout", "reconstruction", "tai tao", "detection",
    "nhan dien", "segmentation", "phan doan", "tracking", "theo doi video",
    "dicom", "3d model", "training", "huan luyen", "bieu do", "gioi thieu",
    "cai dat", "assistant",
)


def _looks_like_ui_action(text: str) -> bool:
    return any(hint in text for hint in _UI_ACTION_HINT_WORDS)


def _match_desktop_action(task: str) -> dict | None:
    """Recognise the fixed desktop commands without relying on LLM tool calling."""
    text = _normalise_agent_task(task)

    if "doi sang english" in text or "change to english" in text or "switch to english" in text:
        return {"action": "language.change", "language": "en"}
    if "doi sang tieng viet" in text or "change to vietnamese" in text or "switch to vietnamese" in text:
        return {"action": "language.change", "language": "vi"}
    if "change avatar" in text or "doi anh dai dien" in text or "doi avatar" in text:
        return {"action": "admin.change_avatar"}
    if "change password" in text or "doi mat khau" in text:
        return {"action": "admin.change_password"}
    if "logout" in text or "dang xuat" in text:
        return {"action": "admin.logout"}
    if "mail settings" in text or "cai dat mail" in text:
        return {"action": "mail.settings"}
    if ("open mail" in text or "open email" in text or "mo mail" in text
            or "mo email" in text or "open inbox" in text or "mo inbox" in text):
        return {"action": "mail.open"}
    if "close mail" in text or "dong mail" in text:
        return {"action": "mail.close"}
    if "open ai assistant" in text or "mo ai assistant" in text:
        return {"action": "assistant.open"}
    if "close ai assistant" in text or "dong ai assistant" in text:
        return {"action": "assistant.close"}
    if "view training charts" in text or "training charts" in text or "bieu do training" in text:
        return {"action": "ai.view_training_charts"}
    if "training model" in text or "train model" in text or "huan luyen model" in text:
        return {"action": "ai.training_model"}
    if "video tracking" in text or "theo doi video" in text:
        return {"action": "ai.video_tracking"}
    if "run segmentation" in text or "segmentation" in text or "phan doan" in text:
        return {"action": "ai.run_segmentation"}
    if "run detection" in text or "detection" in text or "nhan dien" in text:
        return {"action": "ai.run_detection"}
    if "hide results" in text or "an ket qua" in text:
        return {"action": "ai.hide_results"}
    if "start reconstruction" in text or "run reconstruction" in text or "bat dau tai tao" in text:
        return {"action": "reconstruction.start_reconstruction"}
    if "close view 3d model" in text or "close 3d model" in text or "dong 3d model" in text:
        return {"action": "reconstruction.close_3d_model"}
    if "view 3d model" in text or "show point cloud" in text or "xem 3d model" in text:
        return {"action": "reconstruction.view_3d_model"}
    if "load dicom" in text or "dicom series" in text:
        return {"action": "viewer.load_dicom"}
    if "load images" in text or "tai anh tai tao" in text:
        return {"action": "reconstruction.load_images"}
    if "load 3d" in text or "tai 3d" in text:
        return {"action": "viewer.load_3d"}
    if "load 2d" in text or "tai 2d" in text:
        return {"action": "viewer.load_2d"}
    if text == "about" or text.startswith("about ") or "gioi thieu" in text:
        return {"action": "help.about"}
    if text == "settings" or "admin settings" in text or "cai dat" in text:
        return {"action": "admin.settings"}
    return None


def tool_application_action(params: dict) -> dict:
    """Acknowledge a UI action that is executed by the connected Qt client."""
    canonical_params = _canonical_desktop_action(params)
    if canonical_params is None:
        return {"error": f"Unsupported desktop action: {params.get('action', '')}"}
    action = canonical_params["action"]
    if action == "language.change" and canonical_params.get("language") not in {"vi", "en"}:
        return {"error": "language.change requires language 'vi' or 'en'"}
    return {"success": True, "action": action,
            "message": "Action delegated to the connected Qt desktop client."}


def _execute_approved_patch_file(params: dict) -> dict:
    path = params.get("path", "")
    find_text = params.get("find", "")
    replacement = params.get("replace", "")
    abs_path = _agent_safe_path(path)
    if abs_path is None or not os.path.isfile(abs_path):
        return {"error": f"Invalid or missing file: {path}"}
    if not find_text:
        return {"error": "Patch text must not be empty."}
    try:
        with open(abs_path, "r", encoding="utf-8") as handle:
            content = handle.read()
        matches = content.count(find_text)
        if matches != 1:
            return {"error": f"Patch requires exactly one matching fragment; found {matches}.", "path": path}
        with open(abs_path, "w", encoding="utf-8", newline="") as handle:
            handle.write(content.replace(find_text, replacement, 1))
        return {"success": True, "path": path, "replacements": 1}
    except OSError as error:
        return {"error": f"Unable to patch file: {error}"}


def _execute_approved_create_directory(params: dict) -> dict:
    path = params.get("path", "")
    abs_path = _agent_safe_path(path)
    if abs_path is None:
        return {"error": f"Invalid directory path: {path}"}
    try:
        existed = os.path.isdir(abs_path)
        os.makedirs(abs_path, exist_ok=True)
        return {"success": True, "path": path, "created": not existed}
    except OSError as error:
        return {"error": f"Unable to create directory: {error}"}


_TOOL_EXECUTORS = {
    "read_file": tool_read_file,
    "list_directory": tool_list_directory,
    "search_text": tool_search_text,
    "analyze_code": tool_analyze_code,
    "get_project_status": tool_get_project_status,
    "validate_file": tool_validate_file,
    "application_action": tool_application_action,
    # write_file and run_command are handled specially (require approval)
}

_TOOLS_REQUIRING_APPROVAL = {"write_file", "run_command", "patch_file", "create_directory"}

# ── Pending actions storage (in-memory, per session) ──────────────────────────
_pending_actions: dict = {}  # action_id -> {tool, params, session_id}
_pending_lock = threading.Lock()
_PENDING_ACTIONS_FILE = os.path.join(APP_DATA_DIR, "AITraining", "pending_agent_actions.json")


def _save_pending_actions() -> None:
    """Persist pending approvals so a server restart does not invalidate the UI action."""
    os.makedirs(os.path.dirname(_PENDING_ACTIONS_FILE), exist_ok=True)
    temp_file = _PENDING_ACTIONS_FILE + ".tmp"
    with open(temp_file, "w", encoding="utf-8") as handle:
        json.dump(_pending_actions, handle, ensure_ascii=False)
    os.replace(temp_file, _PENDING_ACTIONS_FILE)


def _load_pending_actions() -> None:
    if not os.path.exists(_PENDING_ACTIONS_FILE):
        return
    try:
        with open(_PENDING_ACTIONS_FILE, "r", encoding="utf-8") as handle:
            saved_actions = json.load(handle)
        cutoff = time.time() - 600
        _pending_actions.update({
            action_id: action for action_id, action in saved_actions.items()
            if action.get("created_at", 0) >= cutoff
        })
    except (OSError, ValueError, TypeError) as error:
        logger.warning("Unable to restore pending agent actions: %s", error)


def _generate_action_id() -> str:
    return hashlib.md5(f"{time.time()}-{threading.current_thread().ident}".encode()).hexdigest()[:12]


def _execute_approved_write_file(params: dict) -> dict:
    """Execute write_file after user approval."""
    path = params.get("path", "")
    content = params.get("content", "")
    abs_path = _agent_safe_path(path)
    if abs_path is None:
        return {"error": f"Đường dẫn không hợp lệ: {path}"}

    try:
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(content)
        return {"success": True, "path": path, "bytes_written": len(content.encode("utf-8"))}
    except Exception as e:
        return {"error": f"Lỗi ghi file: {e}"}


def _execute_approved_run_command(params: dict) -> dict:
    """Execute shell command after user approval."""
    command = params.get("command", "")
    cwd = params.get("cwd", ".")
    timeout = min(params.get("timeout", 30), 120)  # Max 2 minutes

    if cwd == ".":
        abs_cwd = PROJECT_DIR
    else:
        abs_cwd = _agent_safe_path(cwd)
    if abs_cwd is None:
        abs_cwd = PROJECT_DIR

    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=abs_cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        stdout = proc.stdout[:5000] if proc.stdout else ""
        stderr = proc.stderr[:2000] if proc.stderr else ""
        return {
            "command": command,
            "return_code": proc.returncode,
            "stdout": stdout,
            "stderr": stderr,
        }
    except subprocess.TimeoutExpired:
        return {"error": f"Command timed out after {timeout}s: {command}"}
    except Exception as e:
        return {"error": f"Lỗi chạy command: {e}"}


# ── Agent System Prompt ───────────────────────────────────────────────────────

def _build_agent_system_prompt(language: str = "vi") -> str:
    tool_desc_parts = []
    for tool in AGENT_TOOLS:
        params_desc = []
        for pname, pinfo in tool["parameters"].items():
            req = " (required)" if pinfo.get("required") else " (optional)"
            params_desc.append(f"    - {pname}: {pinfo['type']}{req} — {pinfo['description']}")
        params_str = "\n".join(params_desc)
        tool_desc_parts.append(f"  {tool['name']}: {tool['description']}\n    Parameters:\n{params_str}")

    tools_block = "\n\n".join(tool_desc_parts)

    return f"""Bạn là AI Agent chuyên nghiệp cho dự án 3D-Reconstruction.
Bạn có khả năng THỰC THI các tác vụ trên project bằng cách gọi các tools.

## AVAILABLE TOOLS:

{tools_block}

## HOW TO CALL TOOLS:

Khi cần sử dụng tool, trả lời CHÍNH XÁC theo format JSON sau (KHÔNG kèm text khác):

```tool_call
{{"tool": "tool_name", "params": {{"param1": "value1", "param2": "value2"}}}}
```

## RULES:

1. Phân tích yêu cầu người dùng, lên kế hoạch các bước cần thực hiện.
2. Gọi tool từng bước một. Sau mỗi kết quả tool, phân tích và quyết định bước tiếp.
3. Khi đã có đủ thông tin, trả lời người dùng bằng text bình thường (KHÔNG gọi tool).
4. Luôn đọc file trước khi sửa — KHÔNG viết file mà chưa đọc nội dung gốc.
5. Mỗi lần chỉ gọi MỘT tool duy nhất.
6. Khi gọi tool write_file hoặc run_command, hệ thống sẽ yêu cầu người dùng phê duyệt.
7. Trả lời bằng tiếng Việt trừ khi được hỏi bằng tiếng Anh.
8. Sử dụng Markdown formatting cho câu trả lời cuối cùng.
9. Nếu task quá lớn hoặc nguy hiểm, hãy giải thích và hỏi lại trước khi thực hiện.
10. Scope: chỉ làm việc trong thư mục project — không truy cập file ngoài project.

11. For application UI requests (loading data, reconstruction, AI tools, mail, language, help, or account actions), you MUST call application_action. Do not substitute source-code tools. Call it once for each requested UI action. This overrides rule 3 — even if you already have relevant text in context (including RELEVANT DOCUMENTATION below), a request to change the app language is a UI action, NOT a request to translate that text. Never respond with a translated passage when the user is asking to switch the interface language.

## EXAMPLE:

User: đổi project sang tiếng việt giúp tôi
Assistant:
```tool_call
{{"tool": "application_action", "params": {{"action": "language.change", "language": "vi"}}}}
```

## PROJECT INFORMATION:
- Project root: {_safe_relpath(PROJECT_DIR, PROJECT_DIR)} (thư mục gốc)
- Ngôn ngữ chính: C++ (Qt), Python
- Build system: CMake

## RESPONSE LANGUAGE:
Respond to the user in {"Vietnamese" if language == "vi" else "English"}. Keep tool names and JSON keys unchanged.
"""


def _parse_tool_call(response_text: str) -> tuple:
    """
    Parse tool_call from LLM response.
    Returns (tool_name, params_dict) or (None, None) if not a tool call.
    """
    # Look for ```tool_call ... ``` block
    match = re.search(r"```tool_call\s*\n?\s*(\{.*?\})\s*\n?\s*```", response_text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(1))
            tool_name = data.get("tool")
            params = data.get("params", {})
            if tool_name and isinstance(params, dict):
                return tool_name, params
        except (json.JSONDecodeError, KeyError):
            pass

    # Fallback: try to parse the entire response as JSON
    stripped = response_text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            data = json.loads(stripped)
            tool_name = data.get("tool")
            params = data.get("params", {})
            if tool_name and isinstance(params, dict):
                return tool_name, params
        except (json.JSONDecodeError, KeyError):
            pass

    return None, None


def _run_langgraph_agent(system_prompt: str, task: str, session_id: str,
                         temperature: float, language: str, request_started: float,
                         initial_messages: list[dict[str, str]] | None = None,
                         initial_steps: list[dict] | None = None,
                         initial_iteration: int = 0) -> dict:
    """Run the tool loop through LangGraph while preserving the Qt API response."""
    if not LANGGRAPH_AVAILABLE or LocalAgentGraph is None:
        raise HTTPException(
            status_code=503,
            detail="LangGraph is required for Agent mode. Run: pip install -r AITraining/requirements.txt",
        )

    def complete(messages: list[dict[str, str]], current_temperature: float) -> str:
        total_chars = sum(len(message.get("content", "")) for message in messages)
        estimated_tokens = int(total_chars / CHARS_PER_TOKEN)
        if estimated_tokens >= LLM_N_CTX - 512:
            return "Context quá dài, dừng Agent."
        max_tokens = min(2048, max(512, LLM_N_CTX - estimated_tokens - 400))

        def call(msgs: list[dict[str, str]]) -> str:
            with llm_lock:
                response_iter = llm.create_chat_completion(
                    messages=msgs,
                    max_tokens=max_tokens,
                    temperature=current_temperature,
                    repeat_penalty=1.1,
                    stream=True,
                )
                text = ""
                for chunk in response_iter:
                    delta = chunk["choices"][0].get("delta", {})
                    if "content" in delta:
                        text += delta["content"]
                return text

        logger.info("LangGraph gọi Model (messages: %d, estimated_tokens: %d)", len(messages), estimated_tokens)
        answer = call(messages)
        logger.info("LangGraph nhận phản hồi từ Model (length: %d chars)", len(answer))

        # [FIX-13] Self-correction NGAY TRONG vòng lặp LangGraph: ở lượt suy
        # luận đầu tiên (messages chỉ gồm system+user, chưa có tool nào chạy),
        # nếu model trả lời bằng văn bản thường (không phát ```tool_call```)
        # trong khi câu hỏi của người dùng mang dáng dấp một lệnh điều khiển
        # UI (rule #11 trong system prompt), cho model MỘT cơ hội tự sửa bằng
        # một system reminder nhấn mạnh rule #11, trước khi chấp nhận đó là
        # final_answer. Quyết định gọi tool cuối cùng vẫn hoàn toàn do model
        # đưa ra qua đúng cơ chế parse/execute của LangGraph — không bypass.
        if len(messages) == 2:
            tool_name, _ = _parse_tool_call(answer)
            user_task = messages[-1].get("content", "")
            if tool_name is None and _looks_like_ui_action(_normalise_agent_task(user_task)):
                logger.info("LangGraph: chưa thấy tool_call ở lượt đầu nhưng task giống lệnh UI — nhắc lại rule #11 và thử lại")
                reminder = {
                    "role": "system",
                    "content": ("Nhắc lại RULE #11: đây là một yêu cầu điều khiển ứng dụng (application UI "
                                "request). Bạn PHẢI trả lời CHÍNH XÁC bằng một khối ```tool_call``` gọi "
                                "application_action. KHÔNG được trả lời bằng văn bản thường, KHÔNG được dịch "
                                "hay diễn giải bất kỳ nội dung nào — chỉ trả về đúng JSON tool_call theo format "
                                "đã hướng dẫn."),
                }
                retry_answer = call([*messages, reminder])
                logger.info("LangGraph nhận phản hồi từ Model sau khi nhắc lại (length: %d chars)", len(retry_answer))
                retry_tool_name, _ = _parse_tool_call(retry_answer)
                if retry_tool_name is not None:
                    logger.info("LangGraph: model đã tự sửa và phát tool_call ở lần thử lại")
                    return retry_answer
                logger.info("LangGraph: model vẫn không phát tool_call sau khi nhắc — giữ nguyên câu trả lời gốc")

        return answer

    def execute(tool_name: str, params: dict) -> dict:
        if tool_name == "application_action":
            canonical_params = _canonical_desktop_action(params)
            if canonical_params is None:
                return {"error": f"Unsupported desktop action: {params.get('action', '')}"}
            params.clear()
            params.update(canonical_params)
        executor = _TOOL_EXECUTORS.get(tool_name)
        if executor is None:
            return {"error": f"Tool không tồn tại: {tool_name}"}
        return executor(params)

    logger.info("Khởi động LangGraph vòng lặp thực thi tool (session: %s)", session_id)
    graph = LocalAgentGraph(
        complete=complete,      # Gọi model để sinh ra câu trả lời
        parse=_parse_tool_call, # Parse tool_call ra khỏi câu trả lời
        execute=execute,        # Thực thi tool
        needs_approval=lambda tool_name: tool_name in _TOOLS_REQUIRING_APPROVAL, # Kiểm tra xem có cần approval không
        max_iterations=_AGENT_MAX_ITERATIONS, # Số lần lặp tối đa
    )
    messages = initial_messages or [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": task},
    ]
    state = graph.run(messages, session_id, temperature, initial_steps, initial_iteration)
    pending_status = "pending_approval" if state.get("pending_tool") else "completed"
    logger.info("LangGraph hoàn thành vòng lặp execution (iteration: %d, status: %s)", state.get("iteration", 0), pending_status)
    steps = state["steps"]
    pending = state.get("pending_tool")
    if pending:
        action_id = _generate_action_id()
        with _pending_lock:
            _pending_actions[action_id] = {
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
            }
            _save_pending_actions()
        steps.append({
            "type": "pending_approval",
            "action_id": action_id,
            "tool": pending["tool"],
            "params": pending["params"],
            "description": pending["params"].get("description", f"Thực thi {pending['tool']}"),
        })
        return {
            "status": "pending_approval", "session_id": session_id,
            "steps": steps, "action_id": action_id,
            "total_ms": round((time.monotonic() - request_started) * 1000),
        }

    if not any(step["type"] == "final_answer" for step in steps):
        steps.append({"type": "final_answer", "content": "Agent đã kết thúc mà chưa có kết luận."})
    return {
        "status": "completed", "session_id": session_id, "steps": steps,
        "iterations": state["iteration"],
        "total_ms": round((time.monotonic() - request_started) * 1000),
    }


# ── Pydantic models for Agent ─────────────────────────────────────────────────

class AgentExecuteRequest(BaseModel):
    task:        str = Field(..., min_length=1, max_length=4000)
    session_id:  str = Field(default="")
    temperature: float = Field(0.3, ge=0.0, le=1.5)  # Lower temp for agent precision
    language:    str = Field(default="vi", pattern="^(vi|en)$")

class AgentApproveRequest(BaseModel):
    action_id:  str = Field(..., min_length=1)
    approved:   bool = Field(...)
    session_id: str = Field(default="")


# ── Agent Endpoints ───────────────────────────────────────────────────────────

_load_pending_actions()


@app.post("/v1/agent/execute")
def agent_execute(request: AgentExecuteRequest, http_req: Request):
    """
    Execute an agentic task with tool-calling loop.
    Returns a list of steps (tool_call, tool_result, thinking, final_answer, pending_approval).
    """
    _cleanup_pending_actions()
    if llm is None:
        raise HTTPException(status_code=503, detail="LLM chưa khởi tạo")

    req_start = time.monotonic()
    task = request.task
    session_id = request.session_id or "agent_default"
    use_langgraph = USE_LANGGRAPH_AGENT and LANGGRAPH_AVAILABLE
    logger.info("[MODE: AGENT] Task from %s: %s…", http_req.client.host, task[:80].replace("\n", " "))

    # Fixed UI commands do not need model reasoning. Handling them here makes
    # the assistant deterministic, avoids aliases invented by small models and
    # still returns normal agent steps for the Qt client to dispatch.
    desktop_params = _match_desktop_action(task)
    if desktop_params:
        action = desktop_params["action"]
        result = tool_application_action(desktop_params)
        message = (f"Đã gửi lệnh tự động: {action}."
                   if request.language == "vi"
                   else f"Desktop command dispatched: {action}.")
        return {
            "status": "completed",
            "session_id": session_id,
            "steps": [
                {"type": "tool_call", "tool": "application_action", "params": desktop_params, "iteration": 0},
                {"type": "tool_result", "tool": "application_action", "result": result, "iteration": 0},
                {"type": "final_answer", "content": message},
            ],
            "iterations": 0,
            "total_ms": round((time.monotonic() - req_start) * 1000),
        }

    # Build initial messages
    system_prompt = _build_agent_system_prompt(request.language)

    # [FIX-12] Add RAG context if available — nhưng bỏ qua khi task có vẻ là
    # một lệnh điều khiển UI mà fast-path (_match_desktop_action) không nhận
    # diện được trọn vẹn. Nhồi tài liệu RAG không liên quan vào những câu như
    # "đổi project sang tiếng việt" từng khiến model dịch nhầm nội dung RAG
    # thay vì gọi application_action.
    if ENABLE_RAG and knowledge_chunks and not _looks_like_ui_action(_normalise_agent_task(task)):
        doc_ctx, code_ctx, _ = get_context(task)
        if doc_ctx:
            system_prompt += f"\n\n## RELEVANT DOCUMENTATION:\n{doc_ctx[:3000]}"
        if code_ctx:
            system_prompt += f"\n\n## RELEVANT CODE:\n{code_ctx[:3000]}"

    if use_langgraph:
        return _run_langgraph_agent(system_prompt, task, session_id,
                                    request.temperature, request.language, req_start)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": task},
    ]

    steps = []
    iteration = 0

    while iteration < _AGENT_MAX_ITERATIONS:
        iteration += 1

        # Estimate tokens and calculate budget
        total_chars = sum(len(m.get("content", "")) for m in messages)
        estimated_tokens = int(total_chars / CHARS_PER_TOKEN)
        available_tokens = LLM_N_CTX - estimated_tokens - 400
        max_tokens = min(2048, max(512, available_tokens))

        if estimated_tokens >= LLM_N_CTX - 512:
            steps.append({
                "type": "error",
                "content": "Context quá dài, dừng agent loop.",
            })
            break

        # Call LLM
        logger.info("Agent iter %d/%d | tokens≈%d", iteration, _AGENT_MAX_ITERATIONS, estimated_tokens)

        try:
            with llm_lock:
                response_iter = llm.create_chat_completion(
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=request.temperature,
                    repeat_penalty=1.1,
                    stream=True,
                )
                answer = ""
                for chunk in response_iter:
                    delta = chunk["choices"][0].get("delta", {})
                    if "content" in delta:
                        answer += delta["content"]
        except Exception as e:
            logger.error("Agent LLM error at iter %d: %s", iteration, e)
            steps.append({"type": "error", "content": f"Lỗi LLM: {e}"})
            break

        answer = answer.strip()
        if not answer:
            steps.append({"type": "error", "content": "LLM trả về response rỗng."})
            break

        # Parse: is it a tool call or final answer?
        tool_name, tool_params = _parse_tool_call(answer)

        if tool_name == "application_action":
            canonical_params = _canonical_desktop_action(tool_params)
            if canonical_params is not None:
                tool_params = canonical_params

        if tool_name is None:
            # Final answer — no more tool calls
            steps.append({
                "type": "final_answer",
                "content": answer,
            })
            break

        # It's a tool call
        steps.append({
            "type": "tool_call",
            "tool": tool_name,
            "params": tool_params,
            "iteration": iteration,
        })

        # Check if tool requires approval
        if tool_name in _TOOLS_REQUIRING_APPROVAL:
            action_id = _generate_action_id()
            with _pending_lock:
                _pending_actions[action_id] = {
                    "tool": tool_name,
                    "params": tool_params,
                    "session_id": session_id,
                    "task": task,
                    "messages": messages.copy(),
                    "steps": steps.copy(),
                    "iteration": iteration,
                    "temperature": request.temperature,
                    "language": request.language,
                    "created_at": time.time(),
                }
                _save_pending_actions()

            steps.append({
                "type": "pending_approval",
                "action_id": action_id,
                "tool": tool_name,
                "params": tool_params,
                "description": tool_params.get("description", f"Thực thi {tool_name}"),
            })

            # Return immediately — client must approve/reject
            total_ms = (time.monotonic() - req_start) * 1000
            logger.info("Agent paused for approval | action=%s tool=%s | %.0fms", action_id, tool_name, total_ms)
            return {
                "status": "pending_approval",
                "session_id": session_id,
                "steps": steps,
                "action_id": action_id,
                "total_ms": round(total_ms),
            }

        # Execute safe tool
        if tool_name in _TOOL_EXECUTORS:
            try:
                tool_result = _TOOL_EXECUTORS[tool_name](tool_params)
            except Exception as e:
                tool_result = {"error": f"Tool exception: {e}"}
        else:
            tool_result = {"error": f"Tool không tồn tại: {tool_name}"}

        steps.append({
            "type": "tool_result",
            "tool": tool_name,
            "result": tool_result,
            "iteration": iteration,
        })

        # Append to conversation for next iteration
        messages.append({"role": "assistant", "content": answer})

        # Format tool result for LLM
        result_text = json.dumps(tool_result, ensure_ascii=False, indent=2)
        if len(result_text) > 8000:
            result_text = result_text[:8000] + "\n... [truncated]"
        messages.append({
            "role": "user",
            "content": f"Tool `{tool_name}` returned:\n```json\n{result_text}\n```\n\nContinue with your analysis or call another tool if needed.",
        })

    # If we exhausted iterations without final answer
    if not any(s["type"] == "final_answer" for s in steps):
        steps.append({
            "type": "final_answer",
            "content": "⚠️ Agent đã đạt giới hạn iterations mà chưa hoàn thành. "
                       "Vui lòng chia nhỏ task hoặc hỏi cụ thể hơn.",
        })

    total_ms = (time.monotonic() - req_start) * 1000
    logger.info(
        "Agent done | iterations=%d steps=%d | %.0fms",
        iteration, len(steps), total_ms,
    )

    return {
        "status": "completed",
        "session_id": session_id,
        "steps": steps,
        "iterations": iteration,
        "total_ms": round(total_ms),
    }


@app.post("/v1/agent/approve")
def agent_approve(request: AgentApproveRequest, http_req: Request):
    """
    Approve or reject a pending agent action (write_file, run_command).
    If approved, executes the action and resumes the agent loop.
    """
    _cleanup_pending_actions()
    if llm is None:
        raise HTTPException(status_code=503, detail="LLM chưa khởi tạo")

    action_id = request.action_id
    with _pending_lock:
        action = _pending_actions.pop(action_id, None)
        _save_pending_actions()

    if action is None:
        raise HTTPException(status_code=404, detail=f"Action không tồn tại hoặc đã hết hạn: {action_id}")

    if not request.approved:
        # User rejected
        return {
            "status": "rejected",
            "action_id": action_id,
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
    tool_name = action["tool"]
    tool_params = action["params"]

    if tool_name == "write_file":
        tool_result = _execute_approved_write_file(tool_params)
    elif tool_name == "run_command":
        tool_result = _execute_approved_run_command(tool_params)
    elif tool_name == "patch_file":
        tool_result = _execute_approved_patch_file(tool_params)
    elif tool_name == "create_directory":
        tool_result = _execute_approved_create_directory(tool_params)
    else:
        tool_result = {"error": f"Unknown approval tool: {tool_name}"}

    steps = action["steps"]
    steps.append({
        "type": "tool_result",
        "tool": tool_name,
        "action_id": action_id,
        "result": tool_result,
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
        )

    # Continue the agent loop
    iteration = action["iteration"]
    temperature = action["temperature"]
    req_start = time.monotonic()

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
            with llm_lock:
                response_iter = llm.create_chat_completion(
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    repeat_penalty=1.1,
                    stream=True,
                )
                answer = ""
                for chunk in response_iter:
                    delta = chunk["choices"][0].get("delta", {})
                    if "content" in delta:
                        answer += delta["content"]
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

        steps.append({
            "type": "tool_call",
            "tool": tool_name_next,
            "params": tool_params_next,
            "iteration": iteration,
        })

        if tool_name_next in _TOOLS_REQUIRING_APPROVAL:
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
                }
                _save_pending_actions()

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
                "prior_step_count": len(action["steps"]),
                "steps": steps,
                "action_id": new_action_id,
                "total_ms": round(total_ms),
            }

        if tool_name_next in _TOOL_EXECUTORS:
            try:
                tool_result_next = _TOOL_EXECUTORS[tool_name_next](tool_params_next)
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
        "prior_step_count": len(action["steps"]),
        "steps": steps,
        "iterations": iteration,
        "total_ms": round(total_ms),
    }


# Cleanup expired pending actions (older than 10 minutes)
def _cleanup_pending_actions():
    cutoff = time.time() - 600
    with _pending_lock:
        expired = [k for k, v in _pending_actions.items() if v.get("created_at", 0) < cutoff]
        for k in expired:
            del _pending_actions[k]
        if expired:
            _save_pending_actions()
        if expired:
            logger.info("Cleaned up %d expired pending agent actions", len(expired))


# ─── 15. Main ─────────────────────────────────────────────────────────────────
def _print_banner():
    desc = MODELS[MODEL_IDX]["desc"]
    if MODELS[MODEL_IDX].get("is_vision", False):
        vision_str = "YES" if ENABLE_VISION_LLM else "fallback text"
    else:
        vision_str = "no"
    print(f"""
╔══════════════════════════════════════════════════════╗
║         3D-Reconstruction AI Server v2.2             ║
╠══════════════════════════════════════════════════════╣
║  Model   : {desc:<42}║
║  Vision  : {vision_str:<42}║
║  Embed   : {EMBED_MODEL_NAME:<42}║
║  Reranker: {str(USE_RERANKER)+" ("+RERANKER_MODEL+")" if USE_RERANKER else "disabled":<42}║
║  Cache   : {_safe_relpath(CACHE_DIR, BASE_DIR):<42}║
╚══════════════════════════════════════════════════════╝""")


if __name__ == "__main__":
    import uvicorn
    from huggingface_hub import hf_hub_download
    from llama_cpp import Llama
    from sentence_transformers import SentenceTransformer

    _print_banner()

    if ENABLE_RAG:
        # Step 1: Embedding model (đa ngôn ngữ)
        with startup_step(f"Loading embedding model ({EMBED_MODEL_NAME})"):
            _embed = SentenceTransformer(EMBED_MODEL_NAME, cache_folder=EMBED_CACHE)
            embed_model_ref = _embed

        # Step 2: Cross-encoder reranker (optional)
        if USE_RERANKER and not MODELS[MODEL_IDX].get("is_vision", False):
            with startup_step(f"Loading reranker ({RERANKER_MODEL})"):
                try:
                    from sentence_transformers import CrossEncoder
                    _reranker = CrossEncoder(
                        RERANKER_MODEL,
                        max_length   = 512,
                        cache_folder = EMBED_CACHE,
                    )
                    logger.info("Reranker loaded: %s", RERANKER_MODEL)
                except Exception as e:
                    logger.warning("Reranker load failed (%s) — continuing without", e)
                    _reranker = None
        else:
            print("  ⏭   Reranker disabled (USE_RERANKER=False)")

        # Step 3: Scan documents
        with startup_step("Scanning documents"):
            raw_chunks = load_documents()
            if not raw_chunks:
                logger.warning("No documents found — RAG context will be empty")

        # Step 4: RAG index
        with startup_step("Loading/building RAG index"):
            if raw_chunks:
                knowledge_index, knowledge_chunks, bm25_index = \
                    load_or_build_index(raw_chunks, _embed)
            else:
                knowledge_index  = None
                knowledge_chunks = []
                bm25_index       = None
                print("       (skipped — no documents)")
    else:
        print("  ⏭   RAG disabled (ENABLE_RAG=False)")
        _embed = None
        embed_model_ref = None
        _reranker = None
        knowledge_index = None
        knowledge_chunks = []
        bm25_index = None

    if MODELS[MODEL_IDX].get("is_vision", False):
        logger.info("Releasing embedding model before LLM load; retrieval will use BM25 fallback")
        embed_model_ref = None
        _embed = None
    _release_ml_memory()

    # Step 5: Download LLM if needed
    selected   = MODELS[MODEL_IDX]
    if selected.get("is_vision", False) and not ENABLE_VISION_LLM:
        logger.warning("Vision LLM disabled for stability; using text fallback model")
        print("  ⏭   Vision LLM disabled; using text fallback model")
        selected = FALLBACK_TEXT_MODEL
    model_path = os.path.join(MODELS_DIR, selected["filename"])

    if not os.path.exists(model_path):
        with startup_step(f"Downloading {selected['desc']}"):
            hf_hub_download(
                repo_id=selected["repo_id"],
                filename=selected["filename"],
                local_dir=MODELS_DIR,
            )
            logger.info("Downloaded: %s (%.1fGB)",
                        selected["filename"],
                        os.path.getsize(model_path)/1024**3)
    else:
        size_gb = os.path.getsize(model_path)/1024**3
        logger.info("Model cached: %s (%.1fGB)", model_path, size_gb)
        print(f"  ✓  Model found locally: {selected['filename']} ({size_gb:.1f}GB)")

    # Step 5b: Download mmproj (vision model only)
    if selected.get("is_vision", False):
        mmproj_path = os.path.join(MODELS_DIR, selected["mmproj_filename"])
        if not os.path.exists(mmproj_path):
            with startup_step(f"Downloading mmproj ({selected['mmproj_filename']})"):
                hf_hub_download(
                    repo_id=selected["mmproj_repo_id"],
                    filename=selected["mmproj_filename"],
                    local_dir=MODELS_DIR,
                )
                logger.info("Downloaded mmproj: %s (%.1fMB)",
                            selected["mmproj_filename"],
                            os.path.getsize(mmproj_path)/1024**2)
        else:
            size_mb = os.path.getsize(mmproj_path)/1024**2
            logger.info("mmproj cached: %s (%.1fMB)", mmproj_path, size_mb)
            print(f"  ✓  mmproj found locally: {selected['mmproj_filename']} ({size_mb:.0f}MB)")

    # Step 6: Load LLM
    if selected.get("is_vision", False):
        # ── Vision model: cần chat handler + mmproj ──
        with startup_step("Loading VL chat handler"):
            from llama_cpp.llama_chat_format import Qwen25VLChatHandler
            _chat_handler = Qwen25VLChatHandler(
                clip_model_path=os.path.join(MODELS_DIR, selected["mmproj_filename"])
            )
            logger.info("Qwen25VLChatHandler loaded with %s", selected["mmproj_filename"])

        with startup_step(f"Loading LLM Vision ({selected['desc']})"):
            try:
                llm = Llama(
                    model_path   = model_path,
                    chat_handler = _chat_handler,
                    chat_format  = "qwen2.5-vl",
                    n_gpu_layers = 99,
                    n_ctx        = LLM_N_CTX,
                    n_batch      = 256,
                    verbose      = False,
                    use_mmap     = True,
                    use_mlock    = False,
                )
                is_vision_model = True
                active_model_desc = selected["desc"]
                logger.info("Vision model activated: %s", selected["desc"])
            except Exception as e:
                logger.warning("Vision model load failed (%s); retrying on CPU", e)
                _release_ml_memory()
                try:
                    llm = Llama(
                        model_path   = model_path,
                        chat_handler = _chat_handler,
                        chat_format  = "qwen2.5-vl",
                        n_gpu_layers = 0,
                        n_ctx        = LLM_N_CTX,
                        n_batch      = 128,
                        verbose      = False,
                        use_mmap     = True,
                        use_mlock    = False,
                    )
                    is_vision_model = True
                    active_model_desc = selected["desc"] + " — CPU"
                    logger.info("Vision model activated on CPU: %s", selected["desc"])
                except Exception as cpu_e:
                    logger.warning("Vision CPU load failed (%s); falling back to text model", cpu_e)
                    _chat_handler = None
                    fallback = FALLBACK_TEXT_MODEL if os.path.exists(os.path.join(MODELS_DIR, FALLBACK_TEXT_MODEL["filename"])) else MODELS[0]
                    fallback_path = os.path.join(MODELS_DIR, fallback["filename"])
                    if not os.path.exists(fallback_path):
                        hf_hub_download(
                            repo_id=fallback["repo_id"],
                            filename=fallback["filename"],
                            local_dir=MODELS_DIR,
                        )
                    llm = Llama(
                        model_path   = fallback_path,
                        n_gpu_layers = 0,
                        n_ctx        = LLM_N_CTX,
                        n_batch      = 256,
                        verbose      = False,
                        use_mmap     = True,
                        use_mlock    = False,
                    )
                    is_vision_model = False
                    active_model_desc = fallback["desc"]
                    logger.info("Fallback text model activated: %s", fallback["desc"])
    else:
        # ── Text-only model: giữ nguyên flow cũ ──
        with startup_step(f"Loading LLM ({selected['desc']})"):
            try:
                llm = Llama(
                    model_path   = model_path,
                    n_gpu_layers = 99,
                    n_ctx        = LLM_N_CTX,
                    n_batch      = 512,
                    verbose      = False,
                    use_mmap     = True,
                    use_mlock    = False,
                )
            except Exception as e:
                logger.warning("Text model load failed with GPU offload (%s); retrying on CPU", e)
                _release_ml_memory()
                llm = Llama(
                    model_path   = model_path,
                    n_gpu_layers = 0,
                    n_ctx        = LLM_N_CTX,
                    n_batch      = 128,
                    verbose      = False,
                    use_mmap     = True,
                    use_mlock    = False,
                )
        active_model_desc = selected["desc"]

    # Start server
    logger.info("Starting uvicorn on 127.0.0.1:8080")
    uvicorn.run(app, host="127.0.0.1", port=8080, log_level="warning", access_log=False)