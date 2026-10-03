"""Code structure and analysis tools."""
from __future__ import annotations

import ast
import os
import re
from typing import Any

from .file_tools import _agent_safe_path


def tool_analyze_code(params: dict[str, Any]) -> dict[str, Any]:
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
