"""Natural language processing utilities for RAG."""
from __future__ import annotations

import re


def tokenize_vn(text: str) -> list[str]:
    """
    Tokenizer BM25 hỗ trợ Việt + English + code.
    - Xử lý một số từ ghép tiếng Việt cơ bản
    - Loại bỏ stopwords cơ bản
    """
    t = text.lower()
    
    # Nối từ ghép cơ bản
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
    viet_tokens = re.findall(r"[^\x00-\x7f\s.,!?;:()\[\]{}'\"<>/\\|@#$%^&*+=~`]+", t)
    
    tokens = latin_tokens + viet_tokens
    return [tk for tk in tokens if tk not in stopwords]


_PROJECT_ROLE_QUERY_ALIASES = (
    (r"\bteam\s*lead\b|\bteamlead\b|\btruong\s*nhom\b|\btrưởng\s*nhóm\b",
     "team lead trưởng nhóm leader"),
    (r"\bdev\s*manager\b|\bdevmanager\b|\bdevelopment\s*manager\b|\bquản\s*lý\s*phát\s*triển\b",
     "dev manager development manager quản lý phát triển"),
    (r"\bproject\s*manager\b|\bquan\s*ly\s*du\s*an\b|\bquản\s*lý\s*dự\s*án\b|\bpm\b",
     "project manager quản lý dự án pm"),
    (r"\bhr\s*manager\b|\bhrmanager\b|\bhuman\s*resources\s*manager\b|\bquản\s*lý\s*nhân\s*sự\b",
     "hr manager human resources manager quản lý nhân sự"),
    (r"\bky\s*su\b|\bkỹ\s*sư\b|\bengineer\b|\bdeveloper\b",
     "kỹ sư engineer developer"),
)


def expand_project_role_query(query: str) -> str:
    """Make role-title searches robust to English/Vietnamese title variants."""
    normalized = " ".join(tokenize_vn(query))
    aliases = [aliases for pattern, aliases in _PROJECT_ROLE_QUERY_ALIASES
               if re.search(pattern, normalized)]
    if not aliases:
        return query
    return f"{query} {' '.join(aliases)} thông tin vai trò nhiệm vụ trách nhiệm"
