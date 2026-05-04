#!/usr/bin/env python3
"""Extract a lightweight courseware outline from source Markdown/text.

This MVP intentionally avoids calling an LLM. It gives the Strategist a stable
structured draft: title, subject/grade hints, knowledge points, difficulty,
Bloom level, duration, prerequisites, and a first-pass page-type plan.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path


BLOOM_LEVELS = ("remember", "understand", "apply", "analyze", "evaluate", "create")
DIFFICULTIES = ("easy", "medium", "hard")


@dataclass
class KnowledgePoint:
    id: str
    title: str
    description: str
    difficulty: str
    prerequisites: list[str] = field(default_factory=list)
    bloom_level: str = "understand"
    estimated_minutes: int = 5
    keywords: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)


@dataclass
class PageAssignment:
    page_type: str
    title: str
    knowledge_point_id: str | None
    difficulty: str
    bloom_level: str
    estimated_minutes: int


@dataclass
class CourseOutline:
    title: str
    subject: str
    grade_level: str
    total_minutes: int
    knowledge_graph: list[KnowledgePoint]
    page_plan: list[PageAssignment]


SUBJECT_HINTS = {
    "math": ("方程", "函数", "几何", "代数", "公式", "计算", "数学"),
    "chinese": ("语文", "阅读", "写作", "修辞", "古诗", "文言文"),
    "english": ("english", "grammar", "vocabulary", "reading", "listening", "英语"),
    "science": ("实验", "物理", "化学", "生物", "科学", "能量", "物质"),
    "history": ("历史", "朝代", "战争", "制度", "文化", "年代"),
}


def normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n")
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    text = re.sub(r"!\[[^\]]*]\([^)]*\)", "", text)
    text = re.sub(r"\[[^\]]+]\([^)]*\)", lambda m: m.group(0).split("]")[0][1:], text)
    return text


def detect_title(text: str, fallback: str = "未命名课件") -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip()
            if title:
                return title[:80]
        if stripped and len(stripped) <= 80:
            return re.sub(r"[*_`]+", "", stripped)
    return fallback


def detect_subject(text: str, explicit: str | None = None) -> str:
    if explicit:
        return explicit
    lower = text.lower()
    scores = {
        subject: sum(1 for hint in hints if hint.lower() in lower)
        for subject, hints in SUBJECT_HINTS.items()
    }
    best, score = max(scores.items(), key=lambda item: item[1])
    return best if score else "general"


def detect_grade(text: str, explicit: str | None = None) -> str:
    if explicit:
        return explicit
    patterns = [
        r"([一二三四五六七八九十]+年级)",
        r"(小学[一二三四五六]年级)",
        r"(初[一二三]年级)",
        r"(高[一二三]年级)",
        r"(七年级|八年级|九年级)",
        r"(grade\s*\d+)",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, flags=re.I)
        if m:
            return m.group(1)
    return "unknown"


def split_candidates(text: str) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    current_title: str | None = None
    current_lines: list[str] = []
    first_content_heading_seen = False
    for line in text.splitlines():
        stripped = line.strip()
        heading = re.match(r"^(#{1,4})\s+(.+)$", stripped)
        numbered = re.match(r"^(?:\d+[.、]|[一二三四五六七八九十]+[、.])\s*(.+)$", stripped)
        bullet = re.match(r"^[-*]\s+(?:\*\*)?([^：:]{2,32})(?:\*\*)?[：:]\s*(.+)$", stripped)
        if heading or numbered:
            if heading and heading.group(1) == "#" and not first_content_heading_seen:
                first_content_heading_seen = True
                current_title = None
                current_lines = []
                continue
            first_content_heading_seen = True
            if current_title:
                candidates.append((current_title, "\n".join(current_lines).strip()))
            current_title = (heading.group(2) if heading else numbered.group(1)).strip()
            current_lines = []
        elif bullet:
            candidates.append((bullet.group(1).strip(), bullet.group(2).strip()))
        elif current_title and stripped:
            current_lines.append(stripped)
    if current_title:
        candidates.append((current_title, "\n".join(current_lines).strip()))

    if not candidates:
        sentences = re.split(r"(?<=[。！？.!?])\s*", text)
        for i, sentence in enumerate(s for s in sentences if len(s.strip()) >= 12):
            title = sentence.strip()[:24]
            candidates.append((title, sentence.strip()))
            if i >= 7:
                break
    return candidates


def clean_title(title: str) -> str:
    title = re.sub(r"[*_`#]+", "", title)
    title = re.sub(r"^\s*(第?\d+[章节课时页]|页面类型|页面主标题)\s*[：:]\s*", "", title)
    return title.strip(" -—:：")[:48] or "知识点"


def keywords_for(title: str, description: str) -> list[str]:
    tokens = re.findall(r"[\u4e00-\u9fff]{2,8}|[A-Za-z][A-Za-z0-9_-]{2,}", f"{title} {description}")
    stop = {"页面", "标题", "要求", "内容", "学生", "老师", "进行", "通过", "需要", "the", "and", "for"}
    out: list[str] = []
    for token in tokens:
        if token.lower() in stop or token in out:
            continue
        out.append(token)
        if len(out) >= 8:
            break
    return out


def classify_difficulty(title: str, description: str, index: int, total: int) -> str:
    joined = f"{title} {description}"
    hard_cues = ("综合", "应用", "建模", "探究", "分析", "评价", "证明", "挑战", "迁移")
    easy_cues = ("定义", "认识", "了解", "目标", "导入", "回顾", "基础")
    if any(cue in joined for cue in hard_cues) or index >= max(total - 2, 0):
        return "hard"
    if any(cue in joined for cue in easy_cues) or index <= 1:
        return "easy"
    return "medium"


def bloom_for(title: str, description: str, difficulty: str) -> str:
    joined = f"{title} {description}"
    if any(cue in joined for cue in ("创造", "设计", "生成", "创作")):
        return "create"
    if any(cue in joined for cue in ("评价", "判断", "反思", "辩论")):
        return "evaluate"
    if any(cue in joined for cue in ("分析", "比较", "原因", "关系")):
        return "analyze"
    if any(cue in joined for cue in ("应用", "练习", "例题", "解决", "操作")):
        return "apply"
    if difficulty == "easy":
        return "remember"
    return "understand"


def estimated_minutes(difficulty: str, description: str) -> int:
    base = {"easy": 4, "medium": 6, "hard": 8}[difficulty]
    if len(description) > 240:
        base += 1
    return base


def extract_knowledge_points(source_text: str, subject: str | None = None,
                             grade_level: str | None = None,
                             max_points: int = 8) -> CourseOutline:
    text = normalize_text(source_text)
    title = detect_title(text)
    subject_value = detect_subject(text, subject)
    grade_value = detect_grade(text, grade_level)
    raw_candidates = split_candidates(text)

    seen = set()
    selected: list[tuple[str, str]] = []
    for title_candidate, description in raw_candidates:
        cleaned = clean_title(title_candidate)
        if cleaned in seen or len(cleaned) < 2:
            continue
        seen.add(cleaned)
        selected.append((cleaned, description or cleaned))
        if len(selected) >= max_points:
            break

    total = len(selected)
    points: list[KnowledgePoint] = []
    for index, (point_title, description) in enumerate(selected):
        difficulty = classify_difficulty(point_title, description, index, total)
        bloom = bloom_for(point_title, description, difficulty)
        point_id = f"KP{index + 1:02d}"
        prereq = [points[-1].id] if points and difficulty != "easy" else []
        points.append(
            KnowledgePoint(
                id=point_id,
                title=point_title,
                description=description[:240],
                difficulty=difficulty,
                prerequisites=prereq,
                bloom_level=bloom,
                estimated_minutes=estimated_minutes(difficulty, description),
                keywords=keywords_for(point_title, description),
                examples=[description[:80]] if "例" in description or "练习" in description else [],
            )
        )

    page_plan = assign_page_types(points)
    total_minutes = sum(page.estimated_minutes for page in page_plan)
    return CourseOutline(
        title=title,
        subject=subject_value,
        grade_level=grade_value,
        total_minutes=total_minutes,
        knowledge_graph=points,
        page_plan=page_plan,
    )


def assign_page_types(points: list[KnowledgePoint]) -> list[PageAssignment]:
    pages: list[PageAssignment] = [
        PageAssignment("objectives", "教学目标", None, "easy", "understand", 2)
    ]
    for idx, point in enumerate(points, start=1):
        pages.append(
            PageAssignment(
                "concept",
                point.title,
                point.id,
                point.difficulty,
                point.bloom_level,
                point.estimated_minutes,
            )
        )
        if point.difficulty == "hard" or point.bloom_level in {"apply", "analyze"}:
            pages.append(
                PageAssignment(
                    "example",
                    f"{point.title}：例题演示",
                    point.id,
                    point.difficulty,
                    "apply",
                    5,
                )
            )
        if idx % 3 == 0 and idx != len(points):
            pages.append(
                PageAssignment(
                    "quiz",
                    f"随堂练习：{point.title}",
                    point.id,
                    "medium",
                    "apply",
                    4,
                )
            )
    pages.append(PageAssignment("summary", "知识总结", None, "easy", "understand", 3))
    return pages


def outline_to_dict(outline: CourseOutline) -> dict:
    return asdict(outline)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Markdown/text source file")
    parser.add_argument("-o", "--output", type=Path, help="Output JSON path")
    parser.add_argument("--subject", help="Subject override, e.g. math/chinese/science")
    parser.add_argument("--grade", help="Grade-level override")
    parser.add_argument("--max-points", type=int, default=8, help="Maximum knowledge points to extract")
    args = parser.parse_args()

    text = args.source.read_text(encoding="utf-8", errors="replace")
    outline = extract_knowledge_points(
        text,
        subject=args.subject,
        grade_level=args.grade,
        max_points=args.max_points,
    )
    payload = outline_to_dict(outline)
    output = args.output or args.source.with_name(f"{args.source.stem}_course_outline.json")
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] Extracted {len(outline.knowledge_graph)} knowledge point(s)")
    print(f"[OK] Page plan: {len(outline.page_plan)} page(s), estimated {outline.total_minutes} minutes")
    print(f"[OK] Saved: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
