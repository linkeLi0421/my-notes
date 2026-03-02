#!/usr/bin/env python3
import argparse
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


NOTES_ROOT = "notes"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
HEX8_RE = re.compile(r"^[0-9a-f]{8}$")
MOJIBAKE_TOKENS = ("鈥", "锛", "銆", "馃", "锟", "\ufffd")


@dataclass
class ChangeSet:
    path: str
    new_path: Optional[str]
    updated_content: Optional[str]
    reasons: List[str]
    skipped: bool
    skip_reason: str = ""


def slugify(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-")
    return value or "general"


def title_from_slug(slug: str) -> str:
    parts = [p for p in slug.split("-") if p]
    if not parts:
        return "Untitled"
    return " ".join(part.capitalize() for part in parts)


def detect_mojibake(text: str) -> bool:
    hits = sum(text.count(token) for token in MOJIBAKE_TOKENS)
    return hits >= 8 and (hits / max(len(text), 1)) > 0.01


def parse_front_matter(lines: List[str]) -> Tuple[Dict[str, str], int]:
    if not lines or lines[0].strip() != "---":
        return {}, 0
    out: Dict[str, str] = {}
    for idx in range(1, min(len(lines), 120)):
        line = lines[idx].rstrip("\n")
        if line.strip() == "---":
            return out, idx + 1
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        out[key.strip()] = value.strip().strip('"').strip("'")
    return {}, 0


def first_h1_index(lines: List[str], start: int) -> int:
    for i in range(start, len(lines)):
        stripped = lines[i].strip()
        if not stripped:
            continue
        if stripped.startswith("# "):
            return i
    return -1


def split_filename_info(path: str) -> Tuple[str, str, str]:
    base = os.path.basename(path).replace(".md", "")
    if len(base) >= 10 and DATE_RE.match(base[:10]):
        date = base[:10]
        rest = base[11:] if len(base) > 11 and base[10] == "-" else ""
    else:
        date = ""
        rest = base
    parts = [p for p in rest.split("-") if p]
    if parts and HEX8_RE.match(parts[-1]):
        parts = parts[:-1]
    slug = "-".join(parts) if parts else ""
    return base, date, slug


def render_front_matter(
    existing: Dict[str, str],
    title: str,
    date: str,
    project: str,
    topic: str,
) -> List[str]:
    lines = ["---"]
    ordered = ["title", "date", "project", "topic"]
    merged = dict(existing)
    merged["title"] = title
    merged["date"] = date
    merged["project"] = project
    merged["topic"] = topic
    for key in ordered:
        value = merged.get(key, "")
        lines.append(f"{key}: {value}")
    for key, value in existing.items():
        if key in ordered:
            continue
        lines.append(f"{key}: {value}")
    lines.append("---")
    return lines


def process_file(path: str) -> ChangeSet:
    with open(path, "r", encoding="utf-8") as f:
        original = f.read()
    if detect_mojibake(original):
        return ChangeSet(path, None, None, [], True, "mojibake-like content; skipped for manual fix")

    lines = original.splitlines()
    fm, body_start = parse_front_matter(lines)
    reasons: List[str] = []

    _, name_date, name_slug = split_filename_info(path)
    fm_date = fm.get("date", "")
    date = fm_date if DATE_RE.match(fm_date) else name_date
    if not DATE_RE.match(date):
        return ChangeSet(path, None, None, [], True, "missing valid date in front matter/filename")

    topic = slugify(fm.get("topic", "") or name_slug or "general")
    project = fm.get("project", "").strip() or "general"

    h1_idx = first_h1_index(lines, body_start)
    current_h1 = lines[h1_idx][2:].strip() if h1_idx >= 0 else ""
    title = fm.get("title", "").strip() or current_h1 or title_from_slug(topic)

    expected_filename = f"{date}-{topic}.md"
    new_path = path
    if os.path.basename(path) != expected_filename:
        new_path = os.path.join(os.path.dirname(path), expected_filename)
        reasons.append("rename to YYYY-MM-DD-topic-slug.md")

    new_fm_lines = render_front_matter(fm, title, date, project, topic)
    if not fm:
        body = lines
        reasons.append("add front matter")
    else:
        body = lines[body_start:]

    if h1_idx >= 0:
        body_h1_idx = h1_idx - body_start
        if body_h1_idx >= 0 and body[body_h1_idx].strip() != f"# {title}":
            body[body_h1_idx] = f"# {title}"
            reasons.append("sync H1 with front matter title")
    else:
        body = [f"# {title}", ""] + body
        reasons.append("insert missing H1")

    rebuilt = "\n".join(new_fm_lines + [""] + body).rstrip() + "\n"
    if rebuilt != original:
        if "add front matter" not in reasons and fm:
            for req in ("title", "date", "project", "topic"):
                if not fm.get(req):
                    reasons.append(f"fill missing front matter field: {req}")
        if not reasons:
            reasons.append("normalize front matter/H1")
    else:
        if not reasons:
            reasons.append("no change")

    updated_content = rebuilt if rebuilt != original else None
    return ChangeSet(path, new_path if new_path != path else None, updated_content, reasons, False)


def iter_note_files(root: str) -> List[str]:
    out: List[str] = []
    for dirpath, _, filenames in os.walk(root):
        for name in filenames:
            if not name.endswith(".md"):
                continue
            p = os.path.join(dirpath, name)
            rel = p.replace("\\", "/")
            if re.search(r"/\d{4}/\d{4}-\d{2}/", rel):
                out.append(p)
    return sorted(out)


def apply_change(change: ChangeSet) -> None:
    if change.updated_content is not None:
        with open(change.path, "w", encoding="utf-8", newline="\n") as f:
            f.write(change.updated_content)
    if change.new_path:
        if os.path.exists(change.new_path):
            raise RuntimeError(f"target file already exists: {change.new_path}")
        os.replace(change.path, change.new_path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair notes filename/front matter/H1 format.")
    parser.add_argument("--root", default=NOTES_ROOT, help="Notes root directory (default: notes)")
    parser.add_argument("--dry-run", action="store_true", help="Only print changes")
    args = parser.parse_args()

    files = iter_note_files(args.root)
    changes = [process_file(p) for p in files]

    changed = 0
    skipped = 0
    for change in changes:
        rel = os.path.relpath(change.path).replace("\\", "/")
        if change.skipped:
            skipped += 1
            print(f"SKIP  {rel}: {change.skip_reason}")
            continue
        will_change = bool(change.updated_content is not None or change.new_path is not None)
        if will_change:
            changed += 1
            print(f"FIX   {rel}: {', '.join(change.reasons)}")
            if change.new_path:
                print(f"      rename -> {os.path.relpath(change.new_path).replace('\\', '/')}")
            if not args.dry_run:
                apply_change(change)
        else:
            print(f"OK    {rel}")

    mode = "DRY-RUN" if args.dry_run else "APPLY"
    print(f"\n{mode} summary: total={len(files)} changed={changed} skipped={skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
