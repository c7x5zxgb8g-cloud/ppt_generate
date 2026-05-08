"""Flask web service wrapper for PPT Master.

The app provides user/project management, source import/conversion, AI-backed
SVG/notes generation, SVG QA, post-processing, and PPTX export.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import struct
import subprocess
import sys
import threading
import uuid
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from functools import wraps
from html import escape
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

from flask import (
    Flask,
    abort,
    jsonify,
    redirect,
    request,
    send_file,
    send_from_directory,
    session,
)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

REPO_ROOT = Path(__file__).resolve().parents[1]
WEBAPP_DIR = REPO_ROOT / "webapp"
DATA_DIR = WEBAPP_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "ppt_master_web.sqlite3"
SCRIPTS_DIR = REPO_ROOT / "skills" / "ppt-master" / "scripts"


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    env_context = dict(os.environ)
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        value = expand_env_value(value, env_context)
        if key and key not in os.environ:
            os.environ[key] = value
            env_context[key] = value


def expand_env_value(value: str, env_context: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(2)
        return env_context.get(name, match.group(0))

    return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)", replace, value)


def load_runtime_env() -> None:
    load_env_file(REPO_ROOT / ".env")
    load_env_file(WEBAPP_DIR / ".env")


load_runtime_env()

sys.path.insert(0, str(SCRIPTS_DIR))
from project_manager import ProjectManager  # noqa: E402
from project_utils import CANVAS_FORMATS as PM_CANVAS_FORMATS  # noqa: E402
from project_utils import normalize_canvas_format  # noqa: E402

EXECUTOR = ThreadPoolExecutor(max_workers=int(os.getenv("PPT_MASTER_WORKERS", "2")))
DB_LOCK = threading.Lock()

ALLOWED_JOB_TYPES = {"validate", "generate_ppt", "quality_check", "postprocess", "export"}
CANVAS_FORMAT_LABELS = {key: value["name"] for key, value in PM_CANVAS_FORMATS.items()}
SVG_PREVIEW_DIRS = {"output": "svg_output", "final": "svg_final"}
AI_RUNNER_TYPES = {"api", "codex", "claude", "rules", "none", "disabled"}
ALLOWED_REPO_READ_PREFIXES = (
    "AGENTS.md",
    "README.md",
    "README_CN.md",
    "skills/ppt-master/SKILL.md",
    "skills/ppt-master/references",
    "skills/ppt-master/templates",
)
ALLOWED_PROJECT_WRITE_PREFIXES = (
    "svg_output",
    "notes",
    "images/image_prompts.md",
    "images/image_sources.json",
    "design_spec.md",
    "spec_lock.md",
    "total.md",
    "metadata.json",
)
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
ASPECT_RATIOS = {"1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9"}
IMAGE_SIZES = {"512px", "1K", "2K", "4K"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def query_one(sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
    with DB_LOCK, get_db() as conn:
        return conn.execute(sql, params).fetchone()


def query_all(sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    with DB_LOCK, get_db() as conn:
        return conn.execute(sql, params).fetchall()


def execute(sql: str, params: tuple[Any, ...] = ()) -> None:
    with DB_LOCK, get_db() as conn:
        conn.execute(sql, params)
        conn.commit()


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    with DB_LOCK, get_db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                canvas_format TEXT NOT NULL,
                project_path TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                type TEXT NOT NULL,
                status TEXT NOT NULL,
                stage TEXT NOT NULL,
                log TEXT NOT NULL DEFAULT '',
                result_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        conn.commit()


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


def public_user(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "email": row["email"],
        "displayName": row["display_name"],
        "role": row["role"],
        "createdAt": row["created_at"],
    }


def json_error(status: int, code: str, message: str, details: Any = None):
    payload: dict[str, Any] = {"error": {"code": code, "message": message}}
    if details is not None:
        payload["error"]["details"] = details
    return jsonify(payload), status


def current_user() -> sqlite3.Row | None:
    user_id = session.get("user_id")
    if not user_id:
        return None
    return query_one("SELECT * FROM users WHERE id = ?", (user_id,))


def require_auth(fn: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any):
        if current_user() is None:
            return json_error(401, "UNAUTHENTICATED", "请先登录")
        return fn(*args, **kwargs)

    return wrapper


def get_project_for_user(project_id: str) -> sqlite3.Row:
    user = current_user()
    if user is None:
        abort(401)
    project = query_one(
        "SELECT * FROM projects WHERE id = ? AND user_id = ?",
        (project_id, user["id"]),
    )
    if project is None:
        abort(404)
    return project


def sanitize_project_name(name: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in name.strip())
    safe = safe.strip("._")
    while "__" in safe:
        safe = safe.replace("__", "_")
    return safe[:72] or "deck"


def list_project_files(project_path: Path) -> dict[str, list[dict[str, Any]]]:
    def collect(relative_dir: str, suffixes: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
        directory = project_path / relative_dir
        if not directory.exists():
            return []
        files = []
        for path in sorted(directory.iterdir()):
            if not path.is_file():
                continue
            if suffixes and path.suffix.lower() not in suffixes:
                continue
            files.append(
                {
                    "name": path.name,
                    "size": path.stat().st_size,
                    "updatedAt": datetime.fromtimestamp(
                        path.stat().st_mtime, timezone.utc
                    ).isoformat(timespec="seconds"),
                }
            )
        return files

    return {
        "sources": collect("sources"),
        "svgOutput": collect("svg_output", (".svg",)),
        "svgFinal": collect("svg_final", (".svg",)),
        "exports": collect("exports", (".pptx",)),
    }


def list_preview_slides(project_id: str, project_path: Path) -> list[dict[str, str]]:
    slides_by_name: dict[str, dict[str, str]] = {}
    for source, relative_dir in (("output", "svg_output"), ("final", "svg_final")):
        directory = project_path / relative_dir
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.svg")):
            slides_by_name[path.name] = {
                "name": path.name,
                "source": source,
                "url": f"/api/projects/{project_id}/preview/{source}/{quote(path.name)}",
            }
    return [slides_by_name[name] for name in sorted(slides_by_name)]


def project_workflow_status(project_path: Path) -> dict[str, Any]:
    files = list_project_files(project_path)
    source_count = len(files["sources"])
    svg_count = len({item["name"] for item in files["svgOutput"] + files["svgFinal"]})
    export_count = len(files["exports"])

    if export_count:
        phase = "exported"
        label = "已导出"
        message = "PPTX 已生成，可以下载或继续更新源材料后重新导出。"
    elif svg_count:
        phase = "slides_ready"
        label = "可预览"
        message = "SVG 页面已生成，可以预览、检查、后处理并导出 PPTX。"
    elif source_count:
        phase = "sources_ready"
        label = "源材料已就绪"
        message = "源材料已导入并完成转换；点击“生成 PPT”后会自动完成页面生成、检查、后处理和导出。"
    else:
        phase = "empty"
        label = "待导入"
        message = "请先上传文档或导入 URL。导入完成后页面会显示源文件和下一步状态。"

    return {
        "phase": phase,
        "label": label,
        "message": message,
        "sourceCount": source_count,
        "svgCount": svg_count,
        "exportCount": export_count,
    }


def markdown_sources(project_path: Path) -> list[Path]:
    sources_dir = project_path / "sources"
    if not sources_dir.exists():
        return []
    candidates = []
    for path in sorted(sources_dir.iterdir()):
        if not path.is_file():
            continue
        if path.name.endswith(".url.txt"):
            continue
        if path.suffix.lower() in {".md", ".markdown", ".txt", ".csv", ".tsv"}:
            candidates.append(path)
    return candidates


def extract_plain_sections(markdown: str) -> list[dict[str, str]]:
    markdown = normalize_markdown(markdown)
    page_sections = extract_page_prompt_sections(markdown)
    if page_sections:
        return page_sections

    sections: list[dict[str, str]] = []
    current = {"title": "核心内容", "body": ""}
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("<!--") or line.startswith("-->"):
            continue
        heading = re.match(r"^(#{1,3})\s+(.+)$", line)
        if heading:
            if current["body"].strip():
                sections.append(current)
            current = {"title": heading.group(2).strip(), "body": ""}
            continue
        if line.startswith(("!", "|")):
            continue
        line = re.sub(r"`([^`]+)`", r"\1", line)
        line = re.sub(r"\*\*([^*]+)\*\*", r"\1", line)
        line = re.sub(r"^\s*[-*+]\s+", "", line)
        line = re.sub(r"^\s*\d+[.)]\s+", "", line)
        current["body"] += line + "\n"

    if current["body"].strip():
        sections.append(current)
    return sections


def normalize_markdown(markdown: str) -> str:
    replacements = {
        r"\#": "#",
        r"\*": "*",
        r"\-": "-",
        r"\+": "+",
        r"\.": ".",
        r"\(": "(",
        r"\)": ")",
        r"\[": "[",
        r"\]": "]",
    }
    normalized = markdown
    for source, target in replacements.items():
        normalized = normalized.replace(source, target)
    return normalized


def extract_page_prompt_sections(markdown: str) -> list[dict[str, str]]:
    marker_re = re.compile(r"(?:\*\*)?\[?第\s*(\d+)\s*页的提示词\]?(?:\*\*)?", re.I)
    matches = list(marker_re.finditer(markdown))
    if not matches:
        return []

    sections: list[dict[str, str]] = []
    for index, match in enumerate(matches):
        page_no = int(match.group(1))
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        block = markdown[start:end]
        section = page_block_to_section(page_no, block)
        if section:
            sections.append(section)
    return sections


def page_block_to_section(page_no: int, block: str) -> dict[str, str] | None:
    lines = [clean_source_line(line) for line in block.splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return None

    page_type = find_labeled_value(lines, "页面类型")
    title = find_labeled_value(lines, "页面主标题")
    subtitle = find_labeled_value(lines, "副标题") or find_labeled_value(lines, "案发现场")
    if not title:
        title = find_labeled_value(lines, "页面号码") or f"第{page_no}页"
    title = f"第{page_no}页｜{title}"

    body_lines: list[str] = []
    for line in lines:
        if "给 NotebookLM" in line or "页面号码" in line:
            continue
        if "动效提示" in line:
            continue
        if line.startswith("[第") or line.startswith("**[第") or line.startswith("***"):
            continue
        if line.startswith("#"):
            continue
        line = re.sub(r"^\*\*([^*：]+)：\*\*\s*", r"\1：", line)
        if line.startswith("页面主标题"):
            continue
        if line.startswith("页面类型"):
            continue
        if line.startswith(("排版要求", "布局要求", "知识点标签", "视频占位符", "数字人", "左侧", "右侧")):
            body_lines.append(line)
            continue
        if len(line) >= 6:
            body_lines.append(line)

    body = "\n".join(body_lines)
    if subtitle:
        body = f"{subtitle}\n{body}"
    if page_type:
        body = f"页面类型：{page_type}\n{body}"
    return {"title": title, "body": body.strip()}


def find_labeled_value(lines: list[str], label: str) -> str | None:
    pattern = re.compile(rf"^(?:\*\*)?{re.escape(label)}(?:/[^：:]+)?[：:](?:\*\*)?\s*(.+)$")
    for line in lines:
        match = pattern.match(line)
        if match:
            value = match.group(1).strip()
            value = value.strip("* ")
            return value or None
    return None


def clean_source_line(line: str) -> str:
    clean = line.strip()
    clean = re.sub(r"^\s*[-*+]\s+", "", clean)
    clean = re.sub(r"^\s*\d+[.)]\s+", "", clean)
    clean = re.sub(r"`([^`]+)`", r"\1", clean)
    clean = clean.replace("\\", "")
    return clean.strip()


def wrap_text(text: str, max_chars: int) -> list[str]:
    clean = re.sub(r"\s+", " ", text).strip()
    if not clean:
        return []
    lines: list[str] = []
    current = ""
    for token in clean.split(" "):
        if len(current) + len(token) + 1 <= max_chars:
            current = f"{current} {token}".strip()
        else:
            if current:
                lines.append(current)
            while len(token) > max_chars:
                lines.append(token[:max_chars])
                token = token[max_chars:]
            current = token
    if current:
        lines.append(current)
    return lines


def svg_text(lines: list[str], x: int, y: int, size: int, fill: str, weight: str = "400") -> str:
    output = []
    line_height = int(size * 1.55)
    for index, line in enumerate(lines):
        output.append(
            f'<text x="{x}" y="{y + index * line_height}" '
            f'font-family="Arial, sans-serif" font-size="{size}" '
            f'font-weight="{weight}" fill="{fill}">{escape(line)}</text>'
        )
    return "\n  ".join(output)


def build_draft_svg(title: str, body: str, index: int, total: int) -> str:
    title_lines = wrap_text(title, 24)[:2]
    body_lines = []
    for paragraph in body.splitlines():
        body_lines.extend(wrap_text(paragraph, 44))
        if len(body_lines) >= 11:
            break
    body_lines = body_lines[:11]
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">
  <rect x="0" y="0" width="1280" height="720" fill="#F6F7F4"/>
  <rect x="64" y="64" width="1152" height="592" fill="#FFFFFF" stroke="#D8DFDA" stroke-width="2"/>
  <rect x="64" y="64" width="10" height="592" fill="#176B58"/>
  {svg_text(title_lines, 112, 150, 44, "#176B58", "700")}
  {svg_text(body_lines, 114, 280, 26, "#1F2523")}
  <text x="112" y="610" font-family="Arial, sans-serif" font-size="18" fill="#66716D">Generated from imported sources</text>
  <text x="1130" y="610" font-family="Arial, sans-serif" font-size="18" fill="#66716D">{index}/{total}</text>
</svg>
'''


def generate_slides(project_path: Path, project_name: str) -> dict[str, Any]:
    sources = markdown_sources(project_path)
    if not sources:
        raise RuntimeError("没有可用于生成的 Markdown/TXT 源材料")

    sections: list[dict[str, str]] = []
    for source in sources:
        content = source.read_text(encoding="utf-8", errors="replace")
        sections.extend(extract_plain_sections(content))

    if not sections:
        raise RuntimeError("源材料中没有提取到可生成页面的文本内容")

    project_path.joinpath("svg_output").mkdir(parents=True, exist_ok=True)
    project_path.joinpath("notes").mkdir(parents=True, exist_ok=True)
    for old_svg in project_path.joinpath("svg_output").glob("*.svg"):
        old_svg.unlink()
    for old_note in project_path.joinpath("notes").glob("*.md"):
        old_note.unlink()

    selected = sections[:35]
    total = len(selected)

    for index, section in enumerate(selected, start=1):
        filename = f"{index:02d}_slide.svg" if index > 1 else "01_cover.svg"
        note_name = f"{index:02d}_slide.md" if index > 1 else "01_cover.md"
        svg = build_draft_svg(section["title"], section["body"], index, total)
        (project_path / "svg_output" / filename).write_text(svg, encoding="utf-8")
        (project_path / "notes" / note_name).write_text(
            f"# {section['title']}\n\n{section['body'].strip()}\n",
            encoding="utf-8",
        )

    design_spec = project_path / "design_spec.md"
    if not design_spec.exists():
        design_spec.write_text(
            f"# Web Generated Design Specification\n\n"
            f"- Project: {project_name}\n"
            f"- Mode: Web one-click generation\n"
            f"- Canvas: PPT 16:9\n"
            f"- Source count: {len(sources)}\n",
            encoding="utf-8",
        )

    return {
        "slides": total,
        "sources": [source.name for source in sources],
        "message": "页面已生成。",
    }


def clear_generated_slide_files(project_path: Path) -> None:
    for relative_dir, suffixes in (("svg_output", {".svg"}), ("svg_final", {".svg"}), ("notes", {".md"})):
        directory = project_path / relative_dir
        directory.mkdir(parents=True, exist_ok=True)
        for path in directory.iterdir():
            if path.is_file() and path.suffix.lower() in suffixes:
                path.unlink()


def run_full_ppt_generation(job_id: str, project_path: Path, project_name: str) -> dict[str, Any]:
    update_job(job_id, "running", "running_ai_generation")
    generation_result = run_ai_generation(job_id, project_path, project_name)

    update_job(job_id, "running", "checking_svg")
    run_svg_quality_gate(job_id, project_path, project_name)

    update_job(job_id, "running", "splitting_total_md")
    run_command(
        job_id,
        [sys.executable, str(SCRIPTS_DIR / "total_md_split.py"), str(project_path)],
    )

    update_job(job_id, "running", "finalizing_svg")
    run_command(
        job_id,
        [sys.executable, str(SCRIPTS_DIR / "finalize_svg.py"), str(project_path)],
    )

    update_job(job_id, "running", "exporting_pptx")
    run_command(
        job_id,
        [sys.executable, str(SCRIPTS_DIR / "svg_to_pptx.py"), str(project_path)],
    )

    exports = list_project_files(project_path)["exports"]
    return {
        "slides": generation_result["slides"],
        "sources": generation_result["sources"],
        "exports": exports,
        "message": "PPT 已生成，可在导出区域下载。",
    }


def run_svg_quality_gate(job_id: str, project_path: Path, project_name: str) -> None:
    result = run_svg_quality_check(job_id, project_path)
    if result.returncode == 0:
        return

    if get_ai_runner_type() != "api":
        raise RuntimeError("SVG 质量检查失败，请查看任务日志")

    max_repairs = int(os.getenv("PPT_MASTER_REPAIR_ATTEMPTS", "3"))
    checker_output = combined_output(result)
    for attempt in range(1, max_repairs + 1):
        update_job(job_id, "running", f"repairing_svg_{attempt}")
        append_job_log(job_id, f"\n[AI repair] SVG 检查失败，开始第 {attempt}/{max_repairs} 次自动回修。\n")
        run_api_agent_repair(job_id, project_path, project_name, checker_output, attempt)
        update_job(job_id, "running", f"rechecking_svg_{attempt}")
        result = run_svg_quality_check(job_id, project_path)
        checker_output = combined_output(result)
        if result.returncode == 0:
            append_job_log(job_id, "[AI repair] SVG 检查已通过。\n")
            return

    raise RuntimeError("SVG 质量检查仍未通过，已达到自动回修次数上限")


def run_svg_quality_check(job_id: str, project_path: Path) -> subprocess.CompletedProcess[str]:
    return run_command(
        job_id,
        [sys.executable, str(SCRIPTS_DIR / "svg_quality_checker.py"), str(project_path)],
        check=False,
    )


def run_ai_generation(job_id: str, project_path: Path, project_name: str) -> dict[str, Any]:
    runner = get_ai_runner_type()
    if runner in {"", "none", "disabled"}:
        raise RuntimeError("AI 生成服务未配置：请设置 PPT_MASTER_AGENT_RUNNER=api 并配置文本模型 API")

    clear_generated_slide_files(project_path)

    if runner == "api":
        return run_api_agent_generation(job_id, project_path, project_name)
    if runner == "codex":
        command = build_codex_command()
    elif runner == "claude":
        command = build_claude_command()
    elif runner == "rules":
        append_job_log(job_id, "[WARN] 使用规则生成器，仅用于开发兜底，不是 PPT Master 原始 AI workflow。\n")
        return generate_slides(project_path, project_name)
    else:
        raise RuntimeError(f"不支持的 AI runner: {runner}")

    prompt = build_ai_generation_prompt(project_path, project_name)
    run_command_with_input(job_id, command, prompt)

    files = list_project_files(project_path)
    svg_count = len({item["name"] for item in files["svgOutput"] + files["svgFinal"]})
    if svg_count == 0:
        raise RuntimeError("AI runner 已结束，但没有生成 SVG 页面")
    return {
        "slides": svg_count,
        "sources": [source.name for source in markdown_sources(project_path)],
    }


def get_ai_runner_type() -> str:
    runner = os.getenv("PPT_MASTER_AGENT_RUNNER", "api").strip().lower()
    if runner not in AI_RUNNER_TYPES:
        return runner
    return runner


def ai_runtime_status() -> dict[str, Any]:
    runner = get_ai_runner_type()
    api_key = get_llm_api_key()
    model = get_llm_model()
    base_url = get_llm_base_url()
    image_prompt_api_key = get_image_prompt_refinement_api_key()
    image_prompt_model = get_image_prompt_refinement_model()
    image_prompt_base_url = get_image_prompt_refinement_base_url()
    image_prompt_enabled = os.getenv("PPT_MASTER_IMAGE_PROMPT_REFINEMENT", "true").lower() in {"1", "true", "yes"}
    return {
        "runner": runner,
        "provider": os.getenv("PPT_MASTER_LLM_PROVIDER", "openai-compatible").strip() or "openai-compatible",
        "model": model or None,
        "baseUrlConfigured": bool(base_url),
        "apiKeyConfigured": bool(api_key),
        "ready": runner in {"codex", "claude", "rules"} or (runner == "api" and bool(api_key and model)),
        "capabilities": {
            "selfRepair": runner == "api",
            "svgQualityTool": runner == "api",
            "imageSearch": runner == "api",
            "imageGeneration": runner == "api" and bool(os.getenv("IMAGE_BACKEND", "").strip()),
            "imagePromptRefinement": runner == "api" and image_prompt_enabled and bool(image_prompt_api_key and image_prompt_model),
        },
        "imagePromptRefinement": {
            "enabled": image_prompt_enabled,
            "provider": get_image_prompt_refinement_provider(),
            "model": image_prompt_model or None,
            "baseUrlConfigured": bool(image_prompt_base_url),
            "apiKeyConfigured": bool(image_prompt_api_key),
            "configuredSeparately": is_image_prompt_refinement_configured_separately(),
        },
    }


def get_llm_api_key() -> str:
    return (
        os.getenv("PPT_MASTER_LLM_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or os.getenv("OPENROUTER_API_KEY")
        or ""
    ).strip()


def get_llm_base_url() -> str:
    return (os.getenv("PPT_MASTER_LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "").strip()


def get_llm_model() -> str:
    return (
        os.getenv("PPT_MASTER_LLM_MODEL")
        or os.getenv("OPENAI_TEXT_MODEL")
        or "gpt-5.5"
    ).strip()


def get_image_prompt_refinement_api_key() -> str:
    return os.getenv("PPT_MASTER_IMAGE_PROMPT_API_KEY", "").strip()


def get_image_prompt_refinement_base_url() -> str:
    return os.getenv("PPT_MASTER_IMAGE_PROMPT_BASE_URL", "").strip()


def get_image_prompt_refinement_model() -> str:
    return os.getenv("PPT_MASTER_IMAGE_PROMPT_MODEL", "").strip()


def get_image_prompt_refinement_provider() -> str:
    return (
        os.getenv("PPT_MASTER_IMAGE_PROMPT_PROVIDER")
        or "openai-compatible"
    ).strip()


def is_image_prompt_refinement_configured_separately() -> bool:
    return bool(get_image_prompt_refinement_api_key() and get_image_prompt_refinement_model())


def run_api_agent_generation(job_id: str, project_path: Path, project_name: str) -> dict[str, Any]:
    expected_slides = expected_slide_count(project_path)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": build_api_agent_system_prompt()},
        {"role": "user", "content": build_api_agent_user_prompt(project_path, project_name)},
    ]
    max_continuations = int(os.getenv("PPT_MASTER_GENERATION_CONTINUATIONS", "3"))
    for continuation in range(max_continuations + 1):
        run_api_agent_loop(
            job_id,
            project_path,
            messages,
            phase_label="generation" if continuation == 0 else f"generation_continue_{continuation}",
            max_turns=int(os.getenv("PPT_MASTER_LLM_MAX_TURNS", "64")),
        )
        svg_count = project_svg_count(project_path)
        notes_ready = project_notes_ready(project_path)
        if svg_count >= expected_slides and notes_ready:
            break
        if continuation >= max_continuations:
            break
        append_job_log(
            job_id,
            f"\n[AI guard] 生成阶段未满足完成条件：SVG {svg_count}/{expected_slides}，notes/total.md={'ready' if notes_ready else 'missing'}。继续要求 agent 补齐。\n",
        )
        messages.append(
            {
                "role": "user",
                "content": (
                    f"你刚才停止了，但当前项目还不能算完成。源材料要求生成 {expected_slides} 页，"
                    f"当前 svg_output 只有 {svg_count} 页，notes/total.md "
                    f"{'已经存在' if notes_ready else '尚未存在或为空'}。"
                    "请立即继续：逐页补齐缺失的 svg_output/*.svg，并写入 notes/total.md。"
                    "除非图片缺失会阻塞页面表达，否则不要再调用图片生成。生成后调用 run_svg_quality_check。"
                ),
            }
        )

    files = list_project_files(project_path)
    svg_count = len({item["name"] for item in files["svgOutput"] + files["svgFinal"]})
    if svg_count == 0:
        raise RuntimeError("API runner 已结束，但没有生成 SVG 页面")
    if svg_count < expected_slides:
        raise RuntimeError(f"API runner 已结束，但只生成了 {svg_count}/{expected_slides} 个 SVG 页面")
    if not project_notes_ready(project_path):
        raise RuntimeError("API runner 已结束，但没有生成 notes/total.md")
    return {
        "slides": svg_count,
        "sources": [source.name for source in markdown_sources(project_path)],
    }


def project_svg_count(project_path: Path) -> int:
    files = list_project_files(project_path)
    return len({item["name"] for item in files["svgOutput"] + files["svgFinal"]})


def project_notes_ready(project_path: Path) -> bool:
    total_md = project_path / "notes" / "total.md"
    return total_md.exists() and total_md.stat().st_size > 0


def expected_slide_count(project_path: Path) -> int:
    max_page = 0
    marker_re = re.compile(r"第\s*(\d+)\s*页")
    for source in markdown_sources(project_path):
        text = source.read_text(encoding="utf-8", errors="replace")
        for match in marker_re.finditer(text):
            max_page = max(max_page, int(match.group(1)))
    return max(max_page, 1)


def run_api_agent_repair(
    job_id: str,
    project_path: Path,
    project_name: str,
    checker_output: str,
    attempt: int,
) -> None:
    run_api_agent_loop(
        job_id,
        project_path,
        [
            {"role": "system", "content": build_api_agent_system_prompt()},
            {
                "role": "user",
                "content": build_api_agent_repair_prompt(project_path, project_name, checker_output, attempt),
            },
        ],
        phase_label="repair",
        max_turns=int(os.getenv("PPT_MASTER_REPAIR_MAX_TURNS", "16")),
    )


def run_api_agent_loop(
    job_id: str,
    project_path: Path,
    messages: list[dict[str, Any]],
    phase_label: str,
    max_turns: int,
) -> None:
    api_key = get_llm_api_key()
    model = get_llm_model()
    base_url = get_llm_base_url()
    if not api_key:
        raise RuntimeError("文本模型 API Key 未配置：请设置 PPT_MASTER_LLM_API_KEY 或 OPENAI_API_KEY")
    if not model:
        raise RuntimeError("文本模型未配置：请设置 PPT_MASTER_LLM_MODEL")

    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("缺少 openai Python 包，请安装 requirements.txt") from exc

    client_kwargs: dict[str, Any] = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url
    client = OpenAI(**client_kwargs)

    append_job_log(job_id, f"[AI] {phase_label} runner 已启动，model={model}，base_url={'configured' if base_url else 'default'}\n")
    timeout = float(os.getenv("PPT_MASTER_LLM_TIMEOUT", "1800"))
    max_tokens = int(os.getenv("PPT_MASTER_LLM_MAX_TOKENS", "12000"))

    for turn in range(1, max_turns + 1):
        append_job_log(job_id, f"[AI] {phase_label} turn {turn}/{max_turns}\n")
        request_args: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "tools": api_agent_tools(),
            "tool_choice": "auto",
            "parallel_tool_calls": False,
            "temperature": float(os.getenv("PPT_MASTER_LLM_TEMPERATURE", "0.2")),
            "timeout": timeout,
        }
        token_param = os.getenv("PPT_MASTER_LLM_TOKEN_PARAM", "max_tokens").strip()
        if token_param in {"max_tokens", "max_completion_tokens"}:
            request_args[token_param] = max_tokens
        response = client.chat.completions.create(**request_args)
        message = response.choices[0].message
        messages.append(message.model_dump(exclude_none=True))

        if message.content:
            append_job_log(job_id, f"[AI] {message.content.strip()}\n")

        tool_calls = message.tool_calls or []
        if not tool_calls:
            return

        for tool_call in tool_calls:
            tool_name = tool_call.function.name
            raw_args = tool_call.function.arguments or "{}"
            try:
                arguments = json.loads(raw_args)
            except json.JSONDecodeError as exc:
                arguments = {}
                result = {"ok": False, "error": f"工具参数不是合法 JSON: {exc}"}
            else:
                result = handle_api_agent_tool(tool_name, arguments, project_path, job_id=job_id)
            append_job_log(job_id, format_tool_log(tool_name, arguments, result))
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )

    raise RuntimeError(f"API runner 在 {phase_label} 阶段达到最大轮次仍未完成")


def build_api_agent_system_prompt() -> str:
    return """你是 PPT Master 的服务器端 API 执行器。

你的任务不是总结文档，而是根据项目源材料生成可导出的 PPT 页面资产：
- 在项目目录写入 `svg_output/*.svg`
- 在项目目录写入 `notes/*.md`
- 必要时写入 `design_spec.md` 和 `spec_lock.md`
- 需要图片时，先在 `design_spec.md` 的 Image Resource List 中规划资源，再调用图片工具写入 `images/`

执行边界：
- 必须遵守仓库内 AGENTS.md 和 skills/ppt-master/SKILL.md 的工作流语义。
- Web 一键生成场景下，Eight Confirmations 视为已按保守默认值确认，不再向用户提问。
- 源材料如果已经按“第 N 页”组织，生成页数、标题和内容必须逐页对应。
- 不允许生成 Markdown 摘要式 PPT；每页必须是完整 SVG 页面，画布 1280x720，元素使用可编辑 SVG 文本和形状。
- 如果设计需要照片、背景图、插画或外部图片，必须使用 run_image_search 或 run_image_generation；run_image_generation 超时或失败时会自动写入同名占位 PNG 并返回 `placeholder=true`，此时必须继续引用该占位图，不要重试同一图片，不要阻塞整套 PPT 生成。
- 不要为了省成本减少生图数量：只要页面表达需要图片资源，就按需要规划并生成；成本优化只通过低质量档和禁止 `2K/4K` 实现。
- 图片默认使用 `image_size="512px"` 的低质量档；当前 OpenAI 兼容接口仍可能按最低像素预算输出约 1K 尺寸。
- 使用 run_image_generation 前，必须先把整套 PPT 的视觉一致性写入 `images/image_prompts.md`：Deck Style Anchor、固定角色设定、色彩/材质/光照/构图约束、文字禁忌、每张图的用途和页面位置。
- 每次 run_image_generation 的 prompt 必须是面向 image2 的完整图像 brief，不能只是关键词：必须包含同一 PPT 的统一风格约束、具体场景、主体身份、人物外观、任务动作、道具、镜头构图、背景环境、情绪氛围、色彩材质、留白要求、禁止项。
- 生成完 SVG 后应主动调用 run_svg_quality_check；若有 error，读取相关文件并修复，再重新检查。
- 在至少写入 1 个 `svg_output/*.svg` 之前，禁止输出“完成”或“接下来将生成”这类终止性回复。
- 不要暴露服务器文件路径、API Key 或环境变量。

你可以使用受限工具读取仓库规则和项目源材料、写入当前项目输出文件、运行 SVG 检查和图片获取工具。"""


def build_api_agent_user_prompt(project_path: Path, project_name: str) -> str:
    sources = "\n".join(f"- sources/{source.name}" for source in markdown_sources(project_path)) or "- none"
    rules = read_runner_context_file("AGENTS.md", 8000)
    skill = read_runner_context_file("skills/ppt-master/SKILL.md", 18000)
    shared = read_runner_context_file("skills/ppt-master/references/shared-standards.md", 14000)
    canvas = read_runner_context_file("skills/ppt-master/references/canvas-formats.md", 8000)
    image_base = read_runner_context_file("skills/ppt-master/references/image-base.md", 9000)
    image_searcher = read_runner_context_file("skills/ppt-master/references/image-searcher.md", 9000)
    image_generator = read_runner_context_file("skills/ppt-master/references/image-generator.md", 9000)
    return f"""项目名称：{project_name}
画布：ppt169 / 1280x720
源材料：
{sources}

已内置的关键规则摘要如下。需要更多上下文时可用 read_file 读取允许范围内的仓库文件。

--- AGENTS.md ---
{rules}

--- skills/ppt-master/SKILL.md ---
{skill}

--- shared-standards.md ---
{shared}

--- canvas-formats.md ---
{canvas}

--- image-base.md ---
{image_base}

--- image-searcher.md ---
{image_searcher}

--- image-generator.md ---
{image_generator}

请直接读取源材料并完成以下工作：
0. 先 list_files 当前项目根目录、images、svg_output；如果已有 design_spec.md、spec_lock.md 或 images/*，优先复用，不要重复生成同类图片。
1. 生成 `design_spec.md` 和 `spec_lock.md`，Web 一键模式下 Eight Confirmations 视为已确认。
2. 如果页面需要图片，按页面设计需要生成足量图片资源，不要为了省成本减少图片数量。图片默认 `image_size` 用 `512px` 低质量档；只有用户明确要求高清或需要大幅裁切时才使用 `1K`，不要使用 `2K/4K`。
   - 先写入或更新 `images/image_prompts.md`，文件开头必须包含 `## Deck Image Contract`，至少列出：Deck Style Anchor、固定角色/人物设定、全局色彩、材质和线条、光照、镜头语言、文本策略、负面约束。
   - 每个 AI 图片资源必须有独立 brief：Slide、Filename、Purpose、Type、Scene、Subject/Characters、Character continuity、Action/gesture、Props、Composition、Background、Mood、Color/material、Negative constraints、Aspect ratio。
   - 调用 `run_image_generation` 时，prompt 使用英文完整自然语言，建议 120-260 词；禁止只写 “cute character, high quality” 这类短关键词。
   - 同一角色跨图必须复用相同外观锚点，例如年龄/服装/发型/表情气质/道具/轮廓；同一 PPT 的背景、插画和图标必须复用同一 Deck Style Anchor。
3. 逐页生成 `svg_output/*.svg`，页数必须匹配源材料页码；同时必须写入 `notes/total.md`，用于后处理拆分。
4. 调用 `run_svg_quality_check`，若有 error，修复后重新检查。
完成时用自然语言简短说明生成页数和图片处理情况。"""


def build_api_agent_repair_prompt(
    project_path: Path,
    project_name: str,
    checker_output: str,
    attempt: int,
) -> str:
    sources = "\n".join(f"- sources/{source.name}" for source in markdown_sources(project_path)) or "- none"
    return f"""项目名称：{project_name}
当前是第 {attempt} 次 SVG 自动回修。

源材料：
{sources}

SVG 质量检查输出如下：
{truncate_text(checker_output, 50000)}

请只修复质量检查中提到的 SVG / spec_lock / notes 问题：
- 用 read_file 读取相关文件
- 用 write_project_file 覆盖修复后的文件
- 修复后调用 run_svg_quality_check
- 如果仍有 error，继续修复直到检查通过或你已经做完本轮可修复内容

不要重写无关页面，不要改变项目主题，不要输出后端路径。"""


def read_runner_context_file(relative_path: str, max_chars: int) -> str:
    path = (REPO_ROOT / relative_path).resolve()
    if not path.exists() or not path.is_file():
        return f"[missing: {relative_path}]"
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) > max_chars:
        return text[:max_chars] + "\n[truncated]"
    return text


def api_agent_tools() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "list_files",
                "description": "列出当前项目或允许的仓库目录文件。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "scope": {"type": "string", "enum": ["project", "repo"]},
                        "path": {"type": "string", "description": "相对路径，默认空字符串。"},
                    },
                    "required": ["scope"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "读取当前项目文件或允许的仓库规则文件。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "scope": {"type": "string", "enum": ["project", "repo"]},
                        "path": {"type": "string"},
                        "start": {"type": "integer", "minimum": 0},
                        "max_chars": {"type": "integer", "minimum": 1000, "maximum": 80000},
                    },
                    "required": ["scope", "path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "write_project_file",
                "description": "写入当前项目的 SVG、notes 或设计规格文件。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "run_svg_quality_check",
                "description": "运行 PPT Master SVG 质量检查。返回 errors/warnings 的命令输出，用于生成后的自检和回修。",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "run_image_search",
                "description": "调用原项目 image_search.py 搜索并下载开放许可图片到当前项目 images/。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "2-8 个关键词或短语。"},
                        "filename": {"type": "string", "description": "保存文件名，如 cover_bg.jpg。"},
                        "orientation": {
                            "type": "string",
                            "enum": ["any", "landscape", "portrait", "square"],
                            "description": "图片方向。",
                        },
                        "purpose": {"type": "string", "description": "用途，如 cover background。"},
                        "slide": {"type": "string", "description": "关联页面，如 01_cover。"},
                        "strict_no_attribution": {"type": "boolean"},
                        "min_width": {"type": "integer", "minimum": 256, "maximum": 8000},
                        "min_height": {"type": "integer", "minimum": 256, "maximum": 8000},
                    },
                    "required": ["query", "filename"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "run_image_generation",
                "description": "调用原项目 image_gen.py 使用配置好的 image2 图片模型生成图片到当前项目 images/。prompt 必须是完整图像 brief，包含整套 PPT 的统一视觉约束、具体场景、主体、人物形象、动作、构图、氛围和负面约束。若生图超时或失败，工具会写入同名占位 PNG 并返回 placeholder=true；调用方应继续引用该图片，不要重试阻塞。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prompt": {
                            "type": "string",
                            "description": "面向 image2 的英文完整提示词，建议 120-260 词；必须包含 Deck Style Anchor、场景、主体/人物、动作、道具、构图、背景、情绪、色彩材质、留白和禁止项。",
                        },
                        "filename": {"type": "string", "description": "文件基础名或图片文件名，如 cover_bg。"},
                        "aspect_ratio": {"type": "string", "enum": sorted(ASPECT_RATIOS)},
                        "image_size": {"type": "string", "enum": sorted(IMAGE_SIZES)},
                        "slide": {"type": "string", "description": "关联页面，如 01_cover 或 第2页。"},
                        "purpose": {"type": "string", "description": "图片在该页中的用途，如 full-bleed background、character sticker、scene illustration。"},
                        "asset_role": {"type": "string", "description": "Background / Illustration / Character / Object / Decorative 等。"},
                        "backend": {"type": "string", "description": "可选，覆盖 IMAGE_BACKEND。"},
                        "model": {"type": "string", "description": "可选，覆盖图片模型。"},
                    },
                    "required": ["prompt", "filename"],
                },
            },
        },
    ]


def handle_api_agent_tool(
    tool_name: str,
    arguments: dict[str, Any],
    project_path: Path,
    job_id: str | None = None,
) -> dict[str, Any]:
    try:
        if tool_name == "list_files":
            return tool_list_files(arguments, project_path)
        if tool_name == "read_file":
            return tool_read_file(arguments, project_path)
        if tool_name == "write_project_file":
            return tool_write_project_file(arguments, project_path)
        if tool_name == "run_svg_quality_check":
            return tool_run_svg_quality_check(project_path)
        if tool_name == "run_image_search":
            return tool_run_image_search(arguments, project_path, job_id)
        if tool_name == "run_image_generation":
            return tool_run_image_generation(arguments, project_path, job_id)
        return {"ok": False, "error": f"未知工具：{tool_name}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def tool_list_files(arguments: dict[str, Any], project_path: Path) -> dict[str, Any]:
    scope = str(arguments.get("scope", "project"))
    relative = str(arguments.get("path", "")).strip()
    directory = resolve_agent_path(scope, relative, project_path, for_write=False)
    if not directory.exists():
        return {"ok": False, "error": "目录不存在"}
    if not directory.is_dir():
        return {"ok": False, "error": "目标不是目录"}
    items = []
    for item in sorted(directory.iterdir()):
        if item.name.startswith("."):
            continue
        items.append(
            {
                "name": item.name,
                "type": "dir" if item.is_dir() else "file",
                "size": item.stat().st_size if item.is_file() else None,
            }
        )
    return {"ok": True, "items": items[:200]}


def tool_read_file(arguments: dict[str, Any], project_path: Path) -> dict[str, Any]:
    scope = str(arguments.get("scope", "project"))
    relative = str(arguments.get("path", "")).strip()
    start = int(arguments.get("start", 0) or 0)
    max_chars = min(max(int(arguments.get("max_chars", 30000) or 30000), 1000), 80000)
    path = resolve_agent_path(scope, relative, project_path, for_write=False)
    if not path.exists() or not path.is_file():
        return {"ok": False, "error": "文件不存在"}
    text = path.read_text(encoding="utf-8", errors="replace")
    chunk = text[start : start + max_chars]
    return {
        "ok": True,
        "path": relative,
        "start": start,
        "nextStart": start + len(chunk) if start + len(chunk) < len(text) else None,
        "content": chunk,
    }


def tool_write_project_file(arguments: dict[str, Any], project_path: Path) -> dict[str, Any]:
    relative = str(arguments.get("path", "")).strip()
    content = str(arguments.get("content", ""))
    path = resolve_agent_path("project", relative, project_path, for_write=True)
    if path.suffix.lower() == ".svg" and "<svg" not in content[:500]:
        return {"ok": False, "error": "SVG 文件内容必须包含 <svg> 根元素"}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return {"ok": True, "path": relative, "bytes": path.stat().st_size}


def tool_run_svg_quality_check(project_path: Path) -> dict[str, Any]:
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "svg_quality_checker.py"), str(project_path)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=int(os.getenv("PPT_MASTER_TOOL_TIMEOUT", "600")),
        check=False,
    )
    return {
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "output": truncate_text(combined_output(result), 60000),
    }


def tool_run_image_search(arguments: dict[str, Any], project_path: Path, job_id: str | None) -> dict[str, Any]:
    query = str(arguments.get("query", "")).strip()
    filename = safe_image_filename(str(arguments.get("filename", "")).strip(), default_ext=".jpg")
    if not query:
        return {"ok": False, "error": "query 不能为空"}

    orientation = str(arguments.get("orientation", "any") or "any")
    if orientation not in {"any", "landscape", "portrait", "square"}:
        orientation = "any"
    images_dir = project_path / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(SCRIPTS_DIR / "image_search.py"),
        query,
        "--filename",
        filename,
        "--output",
        str(images_dir),
        "--orientation",
        orientation,
        "--purpose",
        str(arguments.get("purpose", "") or ""),
        "--slide",
        str(arguments.get("slide", "") or ""),
        "--min-width",
        str(int(arguments.get("min_width", 1200) or 1200)),
        "--min-height",
        str(int(arguments.get("min_height", 800) or 800)),
        "--no-candidates",
    ]
    if bool(arguments.get("strict_no_attribution", False)):
        command.append("--strict-no-attribution")
    result = run_tool_command(command, job_id)
    return image_tool_result(result, images_dir, filename)


def tool_run_image_generation(arguments: dict[str, Any], project_path: Path, job_id: str | None) -> dict[str, Any]:
    prompt = str(arguments.get("prompt", "")).strip()
    filename = safe_image_stem(str(arguments.get("filename", "")).strip())
    if not prompt:
        return {"ok": False, "error": "prompt 不能为空"}
    if not filename:
        return {"ok": False, "error": "filename 不能为空"}

    aspect_ratio = str(arguments.get("aspect_ratio", "16:9") or "16:9")
    if aspect_ratio not in ASPECT_RATIOS:
        aspect_ratio = "16:9"
    image_size = normalize_web_image_size(
        str(arguments.get("image_size", os.getenv("PPT_MASTER_DEFAULT_IMAGE_SIZE", "512px")) or "512px")
    )
    if image_size not in IMAGE_SIZES:
        image_size = "512px"
    images_dir = project_path / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    draft_prompt = build_image2_generation_prompt(prompt, arguments, project_path, filename, aspect_ratio)
    image2_prompt = draft_prompt
    refinement_used = False
    if should_refine_image2_prompt(project_path, prompt):
        if job_id:
            append_job_log(job_id, f"[AI image prompt] refining {filename} with {get_image_prompt_refinement_model()}\n")
        refined_prompt = refine_image2_prompt_with_llm(draft_prompt, arguments, project_path)
        if refined_prompt:
            image2_prompt = refined_prompt
            refinement_used = True
    elif job_id:
        append_job_log(job_id, f"[AI image prompt] skip refinement for {filename}: insufficient deck context\n")
    record_image2_prompt(
        project_path,
        filename,
        prompt,
        draft_prompt,
        image2_prompt,
        refinement_used,
        arguments,
        aspect_ratio,
        image_size,
    )
    before = {path.name for path in images_dir.iterdir() if path.is_file()}
    command = [
        sys.executable,
        str(SCRIPTS_DIR / "image_gen.py"),
        image2_prompt,
        "--aspect_ratio",
        aspect_ratio,
        "--image_size",
        image_size,
        "--output",
        str(images_dir),
        "--filename",
        filename,
    ]
    backend = str(arguments.get("backend", "") or "").strip()
    model = str(arguments.get("model", "") or "").strip()
    if backend:
        command.extend(["--backend", backend])
    if model:
        command.extend(["--model", model])
    timeout = int(os.getenv("PPT_MASTER_IMAGE_TOOL_TIMEOUT", "240"))
    try:
        result = run_tool_command(command, job_id, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        return placeholder_image_generation_result(
            images_dir,
            filename,
            aspect_ratio,
            refinement_used,
            f"image generation timed out after {timeout}s",
            job_id,
            exc,
        )
    after = {path.name for path in images_dir.iterdir() if path.is_file()}
    created = sorted(after - before)
    expected = f"{filename}.png"
    if result.returncode != 0 or expected not in after:
        return placeholder_image_generation_result(
            images_dir,
            filename,
            aspect_ratio,
            refinement_used,
            "image generation failed",
            job_id,
            output=combined_output(result),
            returncode=result.returncode,
        )
    return {
        "ok": True,
        "returncode": result.returncode,
        "created": created,
        "placeholder": False,
        "prompt_refined": refinement_used,
        "output": truncate_text(combined_output(result), 30000),
    }


def placeholder_image_generation_result(
    images_dir: Path,
    filename: str,
    aspect_ratio: str,
    refinement_used: bool,
    reason: str,
    job_id: str | None,
    exc: subprocess.TimeoutExpired | None = None,
    output: str = "",
    returncode: int | None = None,
) -> dict[str, Any]:
    placeholder_path = images_dir / f"{filename}.png"
    write_placeholder_png(placeholder_path, aspect_ratio, filename)
    message = f"[AI image] {reason}; wrote placeholder {placeholder_path.name}\n"
    if job_id:
        append_job_log(job_id, message)
        if exc and exc.stdout:
            append_job_log(job_id, truncate_text(str(exc.stdout), 8000))
        if exc and exc.stderr:
            append_job_log(job_id, truncate_text(str(exc.stderr), 8000))
        if output:
            append_job_log(job_id, truncate_text(output, 8000))
    return {
        "ok": True,
        "returncode": returncode,
        "created": [placeholder_path.name],
        "placeholder": True,
        "prompt_refined": refinement_used,
        "warning": reason,
        "output": truncate_text(output or message, 30000),
    }


def write_placeholder_png(path: Path, aspect_ratio: str, label: str) -> None:
    width, height = placeholder_dimensions(aspect_ratio)
    bg = (242, 238, 229)
    border = (180, 167, 145)
    accent = (217, 79, 79)
    rows = []
    for y in range(height):
        row = bytearray()
        for x in range(width):
            pixel = bg
            if x < 8 or y < 8 or x >= width - 8 or y >= height - 8:
                pixel = border
            elif abs((x / max(width, 1)) - (y / max(height, 1))) < 0.006:
                pixel = accent
            row.extend(pixel)
        rows.append(b"\x00" + bytes(row))
    raw = b"".join(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + png_chunk(b"tEXt", f"placeholder\0{label}".encode("utf-8", errors="replace"))
        + png_chunk(b"IDAT", zlib.compress(raw, level=6))
        + png_chunk(b"IEND", b"")
    )


def png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def placeholder_dimensions(aspect_ratio: str) -> tuple[int, int]:
    mapping = {
        "1:1": (1024, 1024),
        "2:3": (768, 1152),
        "3:2": (1152, 768),
        "3:4": (768, 1024),
        "4:3": (1024, 768),
        "4:5": (768, 960),
        "5:4": (960, 768),
        "9:16": (720, 1280),
        "16:9": (1280, 720),
        "21:9": (1344, 576),
    }
    return mapping.get(aspect_ratio, (1280, 720))


def build_image2_generation_prompt(
    original_prompt: str,
    arguments: dict[str, Any],
    project_path: Path,
    filename: str,
    aspect_ratio: str,
) -> str:
    """Wrap a model-supplied image brief with deck-level consistency context.

    The API agent is still responsible for writing rich prompts. This server-side
    wrapper keeps image2 calls coherent even when the model under-specifies a
    single asset.
    """
    deck_context = deck_image_contract_context(project_path)
    slide = str(arguments.get("slide", "") or "").strip() or "unspecified slide"
    purpose = str(arguments.get("purpose", "") or "").strip() or "presentation visual asset"
    asset_role = str(arguments.get("asset_role", "") or "").strip() or "Illustration"
    return f"""Create one image for a PowerPoint deck using gpt-image-2.

PPT deck consistency context:
{deck_context}

Image asset brief:
- Filename: {filename}
- Slide: {slide}
- Purpose in slide: {purpose}
- Asset role: {asset_role}
- Aspect ratio: {aspect_ratio}
- Original art direction from the PPT agent: {original_prompt}

Image2 generation requirements:
Use the deck consistency context as hard visual continuity. Render a single coherent image, not a collage. Make the scene concrete and readable at presentation size: clear main subject, obvious action, strong silhouette, clean background hierarchy, and composition that fits the stated slide purpose. Preserve recurring character identity across the deck: same species/person type, age impression, outfit, hairstyle or headwear, facial expression language, props, line style, and color accents whenever mentioned in the deck contract. Use the declared color palette, material texture, lighting, and illustration/photography style. If this is a background, reserve quiet negative space for slide text. If this is a character or object asset, keep it isolated or minimally grounded so it can be placed on an SVG slide.

Avoid readable text, logos, watermarks, UI screenshots, extra limbs, distorted faces, inconsistent character design, random new costumes, cluttered details, photorealism unless explicitly requested, and any visual element that conflicts with the PPT source material."""


def deck_image_contract_context(project_path: Path) -> str:
    parts: list[str] = []
    for relative, max_chars in (
        ("images/image_prompts.md", 5000),
        ("spec_lock.md", 5000),
        ("design_spec.md", 7000),
    ):
        path = project_path / relative
        if not path.exists() or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        if text:
            parts.append(f"--- {relative} ---\n{truncate_text(text, max_chars)}")
    if not parts:
        return (
            "No explicit deck contract file exists yet. Use the current prompt as the source of truth, "
            "and keep a consistent presentation illustration style, restrained palette, clean composition, "
            "and no readable text unless explicitly requested."
        )
    return truncate_text("\n\n".join(parts), 12000)


def should_refine_image2_prompt(project_path: Path, original_prompt: str) -> bool:
    if os.getenv("PPT_MASTER_IMAGE_PROMPT_REFINEMENT", "true").lower() not in {"1", "true", "yes"}:
        return False
    if not get_image_prompt_refinement_api_key() or not get_image_prompt_refinement_model():
        return False
    context_chars = 0
    has_design_lock = False
    for relative in ("images/image_prompts.md", "spec_lock.md", "design_spec.md"):
        path = project_path / relative
        if path.exists() and path.is_file():
            size = path.stat().st_size
            context_chars += size
            if relative in {"spec_lock.md", "design_spec.md"} and size >= 300:
                has_design_lock = True
    return has_design_lock and (context_chars + len(original_prompt)) >= 900


def refine_image2_prompt_with_llm(
    draft_prompt: str,
    arguments: dict[str, Any],
    project_path: Path,
) -> str:
    try:
        from openai import OpenAI
    except ImportError:
        return ""

    api_key = get_image_prompt_refinement_api_key()
    model = get_image_prompt_refinement_model()
    if not api_key or not model:
        return ""

    client_kwargs: dict[str, Any] = {"api_key": api_key}
    base_url = get_image_prompt_refinement_base_url()
    if base_url:
        client_kwargs["base_url"] = base_url

    slide = str(arguments.get("slide", "") or "").strip() or "unspecified slide"
    purpose = str(arguments.get("purpose", "") or "").strip() or "presentation visual asset"
    asset_role = str(arguments.get("asset_role", "") or "").strip() or "Illustration"
    max_words = int(os.getenv("PPT_MASTER_IMAGE_PROMPT_MAX_WORDS", "260"))
    min_words = int(os.getenv("PPT_MASTER_IMAGE_PROMPT_MIN_WORDS", "120"))
    source_excerpt = image_prompt_source_context(project_path)
    system_prompt = (
        "You are an expert image prompt director for gpt-image-2 in a PowerPoint generation pipeline. "
        "Your job is not to concatenate constraints. Synthesize the deck context and asset brief into one polished, "
        "coherent English prompt that gpt-image-2 can render directly. Preserve source meaning and deck consistency. "
        "Do not invent a different story, brand, character identity, or visual style. Return only the final prompt."
    )
    user_prompt = f"""Polish this image prompt before generation.

Deck/source context:
{source_excerpt}

Asset metadata:
- Slide: {slide}
- Purpose: {purpose}
- Asset role: {asset_role}

Draft prompt:
{draft_prompt}

Output requirements:
- Return one English prompt only, no markdown, no bullets, no JSON.
- {min_words}-{max_words} words unless the brief is very simple.
- Make it a unified image direction, not a list pasted together.
- Include the deck style anchor, recurring character continuity, exact scene, subject identity, action/gesture, props, composition, background, mood, color/material, lighting, negative space when needed, and negative constraints.
- If there is not enough evidence for a detail, make the least surprising conservative choice that supports the slide, but do not contradict the source.
- Avoid readable text, logos, watermarks, UI screenshots, inconsistent character design, random costumes, clutter, malformed anatomy, and extra limbs."""

    try:
        client = OpenAI(**client_kwargs)
        request_args: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": float(os.getenv("PPT_MASTER_IMAGE_PROMPT_TEMPERATURE", "0.35")),
            "timeout": float(os.getenv("PPT_MASTER_IMAGE_PROMPT_TIMEOUT", "120")),
        }
        token_param = os.getenv("PPT_MASTER_IMAGE_PROMPT_TOKEN_PARAM", "max_tokens").strip()
        if token_param in {"max_tokens", "max_completion_tokens"}:
            request_args[token_param] = int(os.getenv("PPT_MASTER_IMAGE_PROMPT_MAX_TOKENS", "1800"))
        response = client.chat.completions.create(**request_args)
    except Exception:
        return ""

    content = response.choices[0].message.content or ""
    return clean_refined_image_prompt(content)


def image_prompt_source_context(project_path: Path) -> str:
    parts: list[str] = []
    for relative, max_chars in (
        ("images/image_prompts.md", 6000),
        ("spec_lock.md", 5000),
        ("design_spec.md", 7000),
    ):
        path = project_path / relative
        if path.exists() and path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace").strip()
            if text:
                parts.append(f"--- {relative} ---\n{truncate_text(text, max_chars)}")
    for source in markdown_sources(project_path)[:2]:
        text = source.read_text(encoding="utf-8", errors="replace").strip()
        if text:
            parts.append(f"--- sources/{source.name} ---\n{truncate_text(text, 4000)}")
    return truncate_text("\n\n".join(parts), 18000) if parts else "[No source context available]"


def clean_refined_image_prompt(content: str) -> str:
    text = content.strip()
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.I | re.S).strip()
    if re.match(r"^<think\b", text, flags=re.I):
        prompt_match = re.search(r"(?:final\s+prompt|polished\s+prompt|prompt)\s*:\s*(.+)$", text, flags=re.I | re.S)
        if not prompt_match:
            return ""
        text = prompt_match.group(1).strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:text|markdown)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    text = text.strip().strip('"').strip("'").strip()
    text = re.sub(r"^\s*(final prompt|prompt)\s*:\s*", "", text, flags=re.I)
    if re.search(r"<think\b|</think>|I need to|I should|Let's craft|Refining final prompt", text[:500], flags=re.I):
        return ""
    return text if len(text) >= 120 else ""


def record_image2_prompt(
    project_path: Path,
    filename: str,
    original_prompt: str,
    draft_prompt: str,
    image2_prompt: str,
    refinement_used: bool,
    arguments: dict[str, Any],
    aspect_ratio: str,
    image_size: str,
) -> None:
    images_dir = project_path / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = images_dir / "image_prompts.md"
    existing = prompt_path.read_text(encoding="utf-8", errors="replace") if prompt_path.exists() else ""
    header = ""
    if "## Deck Image Contract" not in existing:
        header = (
            "## Deck Image Contract\n\n"
            "This file records image briefs used by the Web API runner. The PPT agent should keep one shared "
            "Deck Style Anchor, recurring character descriptions, palette, lighting, composition rules, text policy, "
            "and negative constraints here before generating assets.\n\n"
        )
    slide = str(arguments.get("slide", "") or "").strip() or "unspecified"
    purpose = str(arguments.get("purpose", "") or "").strip() or "presentation visual asset"
    asset_role = str(arguments.get("asset_role", "") or "").strip() or "Illustration"
    block = f"""
### Generated Asset: {filename}

| Attribute | Value |
| --------- | ----- |
| Slide | {slide} |
| Purpose | {purpose} |
| Type | {asset_role} |
| Aspect Ratio | {aspect_ratio} |
| Image Size | {image_size} |
| Prompt Refinement | {"LLM polished" if refinement_used else "Structured draft"} |

**Original Prompt**:
{original_prompt}

**Structured Draft Prompt**:
{draft_prompt}

**Image2 Prompt Actually Sent**:
{image2_prompt}
"""
    if header:
        prompt_path.write_text(header + existing + block, encoding="utf-8")
    else:
        prompt_path.write_text(existing + block, encoding="utf-8")


def normalize_web_image_size(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"512", "512p", "512px"}:
        return "512px"
    if normalized in {"1k", "1024", "1024px"}:
        return "1K"
    if normalized in {"2k", "2048", "2048px"}:
        return "2K"
    if normalized in {"4k", "4096", "4096px"}:
        return "4K"
    return value.strip()


def run_tool_command(
    command: list[str],
    job_id: str | None,
    timeout: int | None = None,
) -> subprocess.CompletedProcess[str]:
    if job_id:
        append_job_log(job_id, f"$ {' '.join(redact_command(command))}\n")
    result = subprocess.run(
        command,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout or int(os.getenv("PPT_MASTER_TOOL_TIMEOUT", "600")),
        check=False,
    )
    if job_id:
        append_job_log(job_id, truncate_text(combined_output(result), 20000))
    return result


def image_tool_result(
    result: subprocess.CompletedProcess[str],
    images_dir: Path,
    filename: str,
) -> dict[str, Any]:
    path = images_dir / filename
    return {
        "ok": result.returncode == 0 and path.exists(),
        "returncode": result.returncode,
        "filename": filename,
        "exists": path.exists(),
        "output": truncate_text(combined_output(result), 30000),
    }


def safe_image_filename(value: str, default_ext: str) -> str:
    name = secure_filename(Path(value).name)
    if not name:
        raise RuntimeError("图片文件名不能为空")
    path = Path(name)
    suffix = path.suffix.lower() or default_ext
    if suffix not in IMAGE_EXTENSIONS:
        raise RuntimeError("图片文件名只支持 jpg/png/webp")
    stem = secure_filename(path.stem) or "image"
    return f"{stem}{suffix}"


def safe_image_stem(value: str) -> str:
    name = secure_filename(Path(value).name)
    if not name:
        return ""
    return secure_filename(Path(name).stem or name)


def redact_command(command: list[str]) -> list[str]:
    redacted = []
    image_prompt_index: int | None = None
    for index, item in enumerate(command):
        if str(item).endswith("image_gen.py"):
            image_prompt_index = index + 1
            break
    for index, item in enumerate(command):
        text = str(item)
        if image_prompt_index is not None and index == image_prompt_index:
            text = f"[image2 prompt: {len(text)} chars]"
        redacted.append(redact_log_text(text))
    return redacted


def redact_log_text(text: str) -> str:
    web_projects_root = (REPO_ROOT / "projects" / "web").resolve()
    redacted = re.sub(
        rf"{re.escape(str(web_projects_root))}/[^\s\"']+",
        "[项目目录]",
        text,
    )
    redacted = redacted.replace(str(REPO_ROOT), "[系统目录]")
    redacted = re.sub(r"\[系统目录\]/projects/web/[^\s\"']+", "[项目目录]", redacted)
    data_dir = str(DATA_DIR)
    if data_dir in redacted:
        redacted = redacted.replace(data_dir, "[数据目录]")
    return redacted


def resolve_agent_path(scope: str, relative_path: str, project_path: Path, for_write: bool) -> Path:
    relative = Path(relative_path or ".")
    if relative.is_absolute() or ".." in relative.parts:
        raise RuntimeError("非法路径")

    if scope == "project":
        base = project_path.resolve()
        path = (base / relative).resolve()
        if not path_is_within(path, base):
            raise RuntimeError("非法项目路径")
        if for_write and not is_allowed_project_write(relative_path):
            raise RuntimeError("不允许写入该项目路径")
        return path

    if scope == "repo":
        if for_write:
            raise RuntimeError("不允许写入仓库路径")
        base = REPO_ROOT.resolve()
        path = (base / relative).resolve()
        if not path_is_within(path, base) or not is_allowed_repo_read(relative_path):
            raise RuntimeError("不允许读取该仓库路径")
        return path

    raise RuntimeError("未知 scope")


def path_is_within(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
    except ValueError:
        return False
    return True


def is_allowed_repo_read(relative_path: str) -> bool:
    normalized = str(Path(relative_path or ".")).strip("./")
    return any(normalized == prefix or normalized.startswith(f"{prefix}/") for prefix in ALLOWED_REPO_READ_PREFIXES)


def is_allowed_project_write(relative_path: str) -> bool:
    normalized = str(Path(relative_path or ".")).strip("./")
    return any(normalized == prefix or normalized.startswith(f"{prefix}/") for prefix in ALLOWED_PROJECT_WRITE_PREFIXES)


def format_tool_log(tool_name: str, arguments: dict[str, Any], result: dict[str, Any]) -> str:
    path = arguments.get("path", "")
    scope = arguments.get("scope", "project")
    if tool_name == "write_project_file":
        return f"[AI tool] write_project_file {path} -> {'ok' if result.get('ok') else result.get('error')}\n"
    if tool_name == "read_file":
        content_len = len(str(result.get("content", ""))) if result.get("ok") else 0
        return f"[AI tool] read_file {scope}:{path} -> {content_len} chars\n"
    if tool_name == "list_files":
        count = len(result.get("items", [])) if result.get("ok") else 0
        return f"[AI tool] list_files {scope}:{path} -> {count} items\n"
    if tool_name == "run_svg_quality_check":
        return f"[AI tool] run_svg_quality_check -> {'passed' if result.get('ok') else 'needs_fix'}\n"
    if tool_name == "run_image_search":
        filename = arguments.get("filename", "")
        return f"[AI tool] run_image_search {filename} -> {'ok' if result.get('ok') else result.get('error') or 'failed'}\n"
    if tool_name == "run_image_generation":
        created = ", ".join(result.get("created", [])) if result.get("ok") else result.get("error") or "failed"
        if result.get("placeholder"):
            created = f"{created} (placeholder)"
        return f"[AI tool] run_image_generation -> {created}\n"
    return f"[AI tool] {tool_name} -> {'ok' if result.get('ok') else result.get('error')}\n"


def combined_output(result: subprocess.CompletedProcess[str]) -> str:
    output = ""
    if result.stdout:
        output += result.stdout
    if result.stderr:
        output += result.stderr
    return output


def truncate_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n[truncated]"


def build_codex_command() -> list[str]:
    executable = shutil.which("codex")
    if not executable:
        raise RuntimeError("找不到 codex CLI，请安装或设置 PPT_MASTER_AGENT_RUNNER=claude")
    command = [
        executable,
        "exec",
        "--cd",
        str(REPO_ROOT),
        "--sandbox",
        "workspace-write",
        "--skip-git-repo-check",
        "-",
    ]
    model = os.getenv("PPT_MASTER_CODEX_MODEL", "").strip()
    if model:
        command[2:2] = ["--model", model]
    return command


def build_claude_command() -> list[str]:
    executable = shutil.which("claude")
    if not executable:
        raise RuntimeError("找不到 claude CLI，请安装或设置 PPT_MASTER_AGENT_RUNNER=codex")
    return [
        executable,
        "--print",
        "--permission-mode",
        "acceptEdits",
        "--add-dir",
        str(REPO_ROOT),
    ]


def build_ai_generation_prompt(project_path: Path, project_name: str) -> str:
    sources = "\n".join(f"- {source.name}" for source in markdown_sources(project_path)) or "- none"
    return f"""你正在作为 PPT Master 的 AI 生成执行器运行。

用户在 Web 产品中点击了“生成 PPT”，并已授权你使用默认设计决策直接完成生成，不再向用户发起阻塞式确认。

必须遵守：
1. 先阅读 AGENTS.md 和 skills/ppt-master/SKILL.md。
2. 使用 PPT Master 原本的工作流语义：Strategist → Executor → SVG quality check → finalize_svg → svg_to_pptx。
3. 因为这是 Web 一键生成场景，Eight Confirmations 使用保守默认值并视为用户已确认：
   - 模板：自由设计
   - 画布：PPT 16:9
   - 页数：按照源材料中明确的页码/页面结构生成；若源材料已经按“第 N 页”组织，必须逐页对应
   - 风格：尊重源材料描述
   - 图片：没有可用图片时使用可编辑 SVG 图形/占位符，不调用外部生图
   - 输出：可编辑 PPTX
4. 不要生成 Markdown 摘要式 PPT；必须根据源材料逐页生成 `svg_output/*.svg` 和 `notes/*.md`。
5. 完成后必须运行：
   python3 skills/ppt-master/scripts/svg_quality_checker.py "{project_path}"
   python3 skills/ppt-master/scripts/total_md_split.py "{project_path}"
   python3 skills/ppt-master/scripts/finalize_svg.py "{project_path}"
   python3 skills/ppt-master/scripts/svg_to_pptx.py "{project_path}"

项目名称：{project_name}
项目路径：{project_path}
源文件：
{sources}

请现在直接完成这个项目的 PPT 生成。"""


def run_command_with_input(job_id: str, command: list[str], stdin_text: str) -> subprocess.CompletedProcess[str]:
    append_job_log(job_id, f"$ {' '.join(redact_command(command))}\n")
    timeout = int(os.getenv("PPT_MASTER_AGENT_TIMEOUT", "1800"))
    result = subprocess.run(
        command,
        cwd=REPO_ROOT,
        input=stdin_text,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    append_job_log(job_id, result.stdout)
    append_job_log(job_id, result.stderr)
    if result.returncode != 0:
        raise RuntimeError(f"AI runner 执行失败，退出码 {result.returncode}")
    return result


def list_recent_jobs(project_id: str) -> list[dict[str, Any]]:
    rows = query_all(
        """
        SELECT * FROM jobs
        WHERE project_id = ?
        ORDER BY created_at DESC
        LIMIT 5
        """,
        (project_id,),
    )
    return [serialize_job(row) for row in rows]


def project_payload(project: sqlite3.Row) -> dict[str, Any]:
    project_path = Path(project["project_path"])
    manager = ProjectManager()
    info: dict[str, Any] = {}
    try:
        info = manager.get_project_info(str(project_path))
    except Exception as exc:
        info = {"error": str(exc)}

    return {
        "id": project["id"],
        "name": project["name"],
        "canvasFormat": project["canvas_format"],
        "projectHandle": project["id"][:8],
        "createdAt": project["created_at"],
        "updatedAt": project["updated_at"],
        "info": info,
        "files": list_project_files(project_path),
        "previewSlides": list_preview_slides(project["id"], project_path),
        "workflowStatus": project_workflow_status(project_path),
        "recentJobs": list_recent_jobs(project["id"]),
        "aiRuntime": ai_runtime_status(),
    }


def append_job_log(job_id: str, text: str) -> None:
    if not text:
        return
    text = redact_log_text(text)
    execute(
        """
        UPDATE jobs
        SET log = log || ?, updated_at = ?
        WHERE id = ?
        """,
        (text, utc_now(), job_id),
    )


def update_job(job_id: str, status: str, stage: str, result: Any = None) -> None:
    execute(
        """
        UPDATE jobs
        SET status = ?, stage = ?, result_json = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            status,
            stage,
            json.dumps(result, ensure_ascii=False) if result is not None else None,
            utc_now(),
            job_id,
        ),
    )


def run_command(job_id: str, command: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    append_job_log(job_id, f"$ {' '.join(redact_command(command))}\n")
    result = subprocess.run(
        command,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    append_job_log(job_id, result.stdout)
    append_job_log(job_id, result.stderr)
    if check and result.returncode != 0:
        raise RuntimeError(f"命令失败，退出码 {result.returncode}")
    return result


def run_job(job_id: str) -> None:
    job = query_one("SELECT * FROM jobs WHERE id = ?", (job_id,))
    if job is None:
        return
    project = query_one("SELECT * FROM projects WHERE id = ?", (job["project_id"],))
    if project is None:
        update_job(job_id, "failed", "project_missing", {"message": "项目不存在"})
        return

    project_path = Path(project["project_path"])
    update_job(job_id, "running", "started")

    try:
        if job["type"] == "validate":
            update_job(job_id, "running", "validating")
            manager = ProjectManager()
            valid, errors, warnings = manager.validate_project(str(project_path))
            append_job_log(job_id, "项目结构校验完成\n")
            update_job(
                job_id,
                "succeeded" if valid else "failed",
                "finished",
                {"valid": valid, "errors": errors, "warnings": warnings},
            )
            return

        if job["type"] == "quality_check":
            update_job(job_id, "running", "checking_svg")
            run_command(
                job_id,
                [sys.executable, str(SCRIPTS_DIR / "svg_quality_checker.py"), str(project_path)],
            )
            update_job(job_id, "succeeded", "finished", {"message": "SVG 质量检查完成"})
            return

        if job["type"] == "generate_ppt":
            result = run_full_ppt_generation(job_id, project_path, project["name"])
            execute("UPDATE projects SET updated_at = ? WHERE id = ?", (utc_now(), project["id"]))
            update_job(job_id, "succeeded", "finished", result)
            return

        if job["type"] == "postprocess":
            update_job(job_id, "running", "splitting_total_md")
            run_command(
                job_id,
                [sys.executable, str(SCRIPTS_DIR / "total_md_split.py"), str(project_path)],
            )
            update_job(job_id, "running", "finalizing_svg")
            run_command(
                job_id,
                [sys.executable, str(SCRIPTS_DIR / "finalize_svg.py"), str(project_path)],
            )
            update_job(job_id, "succeeded", "finished", {"message": "SVG 后处理完成"})
            return

        if job["type"] == "export":
            update_job(job_id, "running", "splitting_total_md")
            run_command(
                job_id,
                [sys.executable, str(SCRIPTS_DIR / "total_md_split.py"), str(project_path)],
            )
            update_job(job_id, "running", "finalizing_svg")
            run_command(
                job_id,
                [sys.executable, str(SCRIPTS_DIR / "finalize_svg.py"), str(project_path)],
            )
            update_job(job_id, "running", "exporting_pptx")
            run_command(
                job_id,
                [sys.executable, str(SCRIPTS_DIR / "svg_to_pptx.py"), str(project_path)],
            )
            exports = list_project_files(project_path)["exports"]
            update_job(job_id, "succeeded", "finished", {"exports": exports})
            return

        update_job(job_id, "failed", "invalid_type", {"message": "未知任务类型"})
    except Exception as exc:
        append_job_log(job_id, f"\n[ERROR] {exc}\n")
        update_job(job_id, "failed", "failed", {"message": str(exc)})


def create_handoff_prompt(project: sqlite3.Row) -> str:
    project_path = Path(project["project_path"])
    files = list_project_files(project_path)
    sources = "\n".join(f"- {item['name']}" for item in files["sources"]) or "- 暂无"
    return f"""请使用当前仓库的 PPT Master 工作流继续处理这个 Web 项目：

项目路径：{project_path}
画布格式：{project['canvas_format']}
源文件：
{sources}

必须先阅读：
- skills/ppt-master/SKILL.md
- skills/ppt-master/references/strategist.md
- templates/design_spec_reference.md

请从 Strategist 阶段开始，先给出 Eight Confirmations 并等待用户确认。确认后按严格串行流程生成 design_spec.md、spec_lock.md、逐页 SVG、notes，并运行：
python3 skills/ppt-master/scripts/svg_quality_checker.py "{project_path}"
python3 skills/ppt-master/scripts/finalize_svg.py "{project_path}"
python3 skills/ppt-master/scripts/svg_to_pptx.py "{project_path}"
"""


def project_owned_directory(project: sqlite3.Row) -> Path | None:
    user_root = (REPO_ROOT / "projects" / "web" / project["user_id"]).resolve()
    project_path = Path(project["project_path"]).resolve()
    try:
        project_path.relative_to(user_root)
    except ValueError:
        return None
    if project_path == user_root:
        return None
    return project_path


def delete_project_directory(project: sqlite3.Row) -> tuple[bool, str | None]:
    project_path = project_owned_directory(project)
    if project_path is None:
        return False, "项目目录不在当前用户的 Web 项目空间内，已仅删除数据库记录"
    if not project_path.exists():
        return False, "项目目录已不存在，已删除数据库记录"
    if not project_path.is_dir():
        return False, "项目路径不是目录，已仅删除数据库记录"
    shutil.rmtree(project_path)
    return True, None


def create_app() -> Flask:
    init_db()
    app = Flask(
        __name__,
        static_folder=str(WEBAPP_DIR / "static"),
        template_folder=str(WEBAPP_DIR / "templates"),
    )
    app.config["MAX_CONTENT_LENGTH"] = int(os.getenv("PPT_MASTER_MAX_UPLOAD_MB", "80")) * 1024 * 1024
    app.secret_key = os.getenv("PPT_MASTER_WEB_SECRET", "dev-secret-change-me")

    @app.get("/")
    def index():
        return send_from_directory(WEBAPP_DIR / "templates", "index.html")

    @app.get("/health")
    def health():
        return jsonify({"status": "ok", "time": utc_now(), "aiRuntime": ai_runtime_status()})

    @app.get("/api/runtime")
    @require_auth
    def runtime():
        return jsonify({"aiRuntime": ai_runtime_status()})

    @app.post("/api/auth/register")
    def register():
        if os.getenv("PPT_MASTER_ALLOW_REGISTRATION", "true").lower() not in {"1", "true", "yes"}:
            return json_error(403, "REGISTRATION_DISABLED", "注册已关闭")
        data = request.get_json(silent=True) or {}
        email = str(data.get("email", "")).strip().lower()
        password = str(data.get("password", ""))
        display_name = str(data.get("displayName", "")).strip() or email.split("@")[0]
        if "@" not in email or len(password) < 8:
            return json_error(422, "VALIDATION_ERROR", "邮箱格式不正确或密码少于 8 位")

        user_id = uuid.uuid4().hex
        has_users = query_one("SELECT id FROM users LIMIT 1") is not None
        role = "user" if has_users else "admin"
        try:
            execute(
                """
                INSERT INTO users (id, email, display_name, password_hash, role, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (user_id, email, display_name, generate_password_hash(password), role, utc_now()),
            )
        except sqlite3.IntegrityError:
            return json_error(409, "EMAIL_EXISTS", "这个邮箱已经注册")

        session["user_id"] = user_id
        user = query_one("SELECT * FROM users WHERE id = ?", (user_id,))
        return jsonify({"user": public_user(user)}), 201

    @app.post("/api/auth/login")
    def login():
        data = request.get_json(silent=True) or {}
        email = str(data.get("email", "")).strip().lower()
        password = str(data.get("password", ""))
        user = query_one("SELECT * FROM users WHERE email = ?", (email,))
        if user is None or not check_password_hash(user["password_hash"], password):
            return json_error(401, "INVALID_CREDENTIALS", "邮箱或密码不正确")
        session["user_id"] = user["id"]
        return jsonify({"user": public_user(user)})

    @app.post("/api/auth/logout")
    def logout():
        session.clear()
        return jsonify({"ok": True})

    @app.get("/api/me")
    def me():
        user = current_user()
        return jsonify({"user": public_user(user) if user else None})

    @app.get("/api/projects")
    @require_auth
    def list_projects():
        user = current_user()
        rows = query_all(
            "SELECT * FROM projects WHERE user_id = ? ORDER BY created_at DESC",
            (user["id"],),
        )
        return jsonify({"data": [project_payload(row) for row in rows]})

    @app.post("/api/projects")
    @require_auth
    def create_project():
        user = current_user()
        data = request.get_json(silent=True) or {}
        name = str(data.get("name", "")).strip()
        canvas_format = normalize_canvas_format(str(data.get("canvasFormat", "ppt169")).strip() or "ppt169")
        if not name:
            return json_error(422, "VALIDATION_ERROR", "项目名称不能为空")
        if canvas_format not in PM_CANVAS_FORMATS:
            return json_error(422, "VALIDATION_ERROR", "不支持的画布格式")

        project_id = uuid.uuid4().hex
        base_dir = REPO_ROOT / "projects" / "web" / user["id"]
        safe_name = sanitize_project_name(name)
        manager = ProjectManager(base_dir=str(base_dir))
        try:
            try:
                project_path = Path(manager.init_project(safe_name, canvas_format=canvas_format))
            except FileExistsError:
                project_path = Path(
                    manager.init_project(
                        f"{safe_name}_{project_id[:6]}",
                        canvas_format=canvas_format,
                    )
                )
        except Exception as exc:
            return json_error(500, "PROJECT_CREATE_FAILED", "项目创建失败", str(exc))

        now = utc_now()
        execute(
            """
            INSERT INTO projects (id, user_id, name, canvas_format, project_path, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (project_id, user["id"], name, canvas_format, str(project_path), now, now),
        )
        project = query_one("SELECT * FROM projects WHERE id = ?", (project_id,))
        return jsonify({"data": project_payload(project)}), 201

    @app.get("/api/projects/<project_id>")
    @require_auth
    def get_project(project_id: str):
        project = get_project_for_user(project_id)
        return jsonify({"data": project_payload(project)})

    @app.delete("/api/projects/<project_id>")
    @require_auth
    def delete_project(project_id: str):
        user = current_user()
        project = get_project_for_user(project_id)
        active_job = query_one(
            """
            SELECT id, type, status FROM jobs
            WHERE project_id = ? AND status IN ('queued', 'running')
            LIMIT 1
            """,
            (project_id,),
        )
        if active_job is not None:
            return json_error(409, "PROJECT_HAS_ACTIVE_JOB", "项目有正在运行的任务，完成后再删除")

        try:
            files_deleted, warning = delete_project_directory(project)
        except Exception as exc:
            return json_error(500, "PROJECT_DELETE_FAILED", "项目目录删除失败", str(exc))

        execute("DELETE FROM projects WHERE id = ? AND user_id = ?", (project_id, user["id"]))
        payload: dict[str, Any] = {
            "ok": True,
            "deletedProjectId": project_id,
            "filesDeleted": files_deleted,
        }
        if warning:
            payload["warning"] = warning
        return jsonify(payload)

    @app.post("/api/projects/<project_id>/sources")
    @require_auth
    def import_sources(project_id: str):
        project = get_project_for_user(project_id)
        project_path = Path(project["project_path"])
        manager = ProjectManager()

        imported_items: list[str] = []
        upload_batch = UPLOAD_DIR / uuid.uuid4().hex
        upload_batch.mkdir(parents=True, exist_ok=True)

        for uploaded in request.files.getlist("files"):
            if not uploaded.filename:
                continue
            filename = secure_filename(uploaded.filename)
            if not filename:
                continue
            target = upload_batch / filename
            uploaded.save(target)
            imported_items.append(str(target))

        if request.is_json:
            data = request.get_json(silent=True) or {}
            url = str(data.get("url", "")).strip()
            if url:
                imported_items.append(url)
        else:
            url = str(request.form.get("url", "")).strip()
            if url:
                imported_items.append(url)

        if not imported_items:
            return json_error(422, "VALIDATION_ERROR", "请上传文件或输入 URL")

        try:
            summary = manager.import_sources(str(project_path), imported_items, move=True)
        except Exception as exc:
            return json_error(500, "IMPORT_FAILED", "导入源材料失败", str(exc))

        execute("UPDATE projects SET updated_at = ? WHERE id = ?", (utc_now(), project_id))
        refreshed = query_one("SELECT * FROM projects WHERE id = ?", (project_id,))
        return jsonify({"summary": summary, "data": project_payload(refreshed)})

    @app.post("/api/projects/<project_id>/jobs")
    @require_auth
    def create_job(project_id: str):
        user = current_user()
        get_project_for_user(project_id)
        data = request.get_json(silent=True) or {}
        job_type = str(data.get("type", "validate"))
        if job_type not in ALLOWED_JOB_TYPES:
            return json_error(422, "VALIDATION_ERROR", "不支持的任务类型")

        job_id = uuid.uuid4().hex
        now = utc_now()
        execute(
            """
            INSERT INTO jobs (id, user_id, project_id, type, status, stage, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'queued', 'queued', ?, ?)
            """,
            (job_id, user["id"], project_id, job_type, now, now),
        )
        EXECUTOR.submit(run_job, job_id)
        job = query_one("SELECT * FROM jobs WHERE id = ?", (job_id,))
        return jsonify({"data": serialize_job(job)}), 202

    @app.get("/api/jobs/<job_id>")
    @require_auth
    def get_job(job_id: str):
        user = current_user()
        job = query_one("SELECT * FROM jobs WHERE id = ? AND user_id = ?", (job_id, user["id"]))
        if job is None:
            abort(404)
        return jsonify({"data": serialize_job(job)})

    @app.get("/api/projects/<project_id>/jobs")
    @require_auth
    def list_project_jobs(project_id: str):
        project = get_project_for_user(project_id)
        return jsonify({"data": list_recent_jobs(project["id"])})

    @app.get("/api/projects/<project_id>/handoff-prompt")
    @require_auth
    def handoff_prompt(project_id: str):
        project = get_project_for_user(project_id)
        return jsonify({"prompt": create_handoff_prompt(project)})

    @app.get("/api/projects/<project_id>/preview/<source>/<path:filename>")
    @require_auth
    def preview_slide(project_id: str, source: str, filename: str):
        project = get_project_for_user(project_id)
        if source not in SVG_PREVIEW_DIRS:
            abort(404)
        if Path(filename).name != filename or Path(filename).suffix.lower() != ".svg":
            abort(404)
        preview_dir = Path(project["project_path"]) / SVG_PREVIEW_DIRS[source]
        path = (preview_dir / filename).resolve()
        if not str(path).startswith(str(preview_dir.resolve())) or not path.exists():
            abort(404)
        return send_file(path, mimetype="image/svg+xml")

    @app.get("/api/projects/<project_id>/downloads/<path:filename>")
    @require_auth
    def download(project_id: str, filename: str):
        project = get_project_for_user(project_id)
        exports_dir = Path(project["project_path"]) / "exports"
        path = (exports_dir / filename).resolve()
        if not str(path).startswith(str(exports_dir.resolve())) or not path.exists():
            abort(404)
        return send_file(path, as_attachment=True)

    @app.errorhandler(404)
    def not_found(_error):
        if request.path.startswith("/api/"):
            return json_error(404, "NOT_FOUND", "资源不存在")
        return redirect("/")

    @app.errorhandler(413)
    def too_large(_error):
        return json_error(413, "PAYLOAD_TOO_LARGE", "上传文件过大")

    return app


def serialize_job(job: sqlite3.Row) -> dict[str, Any]:
    result = None
    if job["result_json"]:
        try:
            result = json.loads(job["result_json"])
        except json.JSONDecodeError:
            result = job["result_json"]
    return {
        "id": job["id"],
        "projectId": job["project_id"],
        "type": job["type"],
        "status": job["status"],
        "stage": job["stage"],
        "log": redact_log_text(job["log"]),
        "result": result,
        "createdAt": job["created_at"],
        "updatedAt": job["updated_at"],
    }


app = create_app()


if __name__ == "__main__":
    debug = os.getenv("PPT_MASTER_DEBUG", "false").lower() in {"1", "true", "yes"}
    app.run(
        host=os.getenv("PPT_MASTER_HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "5001")),
        debug=debug,
        use_reloader=debug,
    )
