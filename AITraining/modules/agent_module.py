from .config import *
from .config import _safe_relpath
from . import rag_module as rag_runtime
from . import llm_module as llm_runtime

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
            with llm_runtime.llm_lock:
                response_iter = llm_runtime.llm.create_chat_completion(
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


from fastapi import APIRouter
agent_router = APIRouter()

@agent_router.post("/v1/agent/execute")
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
    if ENABLE_RAG and rag_runtime.knowledge_chunks and not _looks_like_ui_action(_normalise_agent_task(task)):
        doc_ctx, code_ctx, _ = rag_runtime.get_context(task)
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
            with llm_runtime.llm_lock:
                response_iter = llm_runtime.llm.create_chat_completion(
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


@agent_router.post("/v1/agent/approve")
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
            with llm_runtime.llm_lock:
                response_iter = llm_runtime.llm.create_chat_completion(
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


def reset_agent_state() -> None:
    """Forget all pending approvals and their persisted state."""
    with _pending_lock:
        _pending_actions.clear()
        try:
            if os.path.exists(_PENDING_ACTIONS_FILE):
                os.remove(_PENDING_ACTIONS_FILE)
        except OSError as error:
            logger.warning("Unable to remove pending action state: %s", error)


