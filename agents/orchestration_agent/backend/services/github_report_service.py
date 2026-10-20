"""
Grouped CUQA reporting for GitHub
=================================
R26-SE-008 | Bandara S M Y M | IT22277886

Three jobs, all over REAL CUQA findings:

  1. STABLE IDS. Every finding of a workflow gets a short id, S001, S002, ...,
     by its position in the workflow's stored smell list. That list is fixed
     for an analysed commit, so "S005" means the same finding in the Check
     Run, in a PR comment command and in DIWO.

  2. VERIFIED LOCATIONS. A CUQA line number is checked against the file AT
     THE ANALYSED COMMIT before it becomes a GitHub annotation: the line must
     exist, a method/class smell must sit on that symbol's declaration, and a
     MagicNumber must have its literal on that line. A location that can be
     corrected unambiguously (the symbol is declared exactly once nearby) is
     corrected; one that cannot is not annotated — the finding stays in the
     report and in DIWO, it just is not pinned to a line it may not belong to.

  3. GROUPING. Findings are grouped file → smell type, ordered by severity,
     so "MagicNumber — 41 occurrences" is one row rather than 41.
"""

import re
import zipfile
from pathlib import PurePosixPath

from domain.cuqa_normalizer import CLASS_LEVEL_SMELLS, METHOD_LEVEL_SMELLS

SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}
MAX_SOURCE_BYTES_FOR_VERIFICATION = 2 * 1024 * 1024
#: How far from the reported line a symbol/literal may be found and corrected.
SEARCH_WINDOW = 3

_MAGIC_RE = re.compile(r"[Mm]agic number\s+(-?\d+(?:\.\d+)?)")


# ─── Stable ids ─────────────────────────────────────────────────────────────

def display_ids(smells: list) -> dict:
    """{internal smell id: "S001"} in stored order."""
    return {s["id"]: f"S{index:03d}" for index, s in enumerate(smells, start=1)}


def resolve_display_ids(smells: list, tokens: list) -> tuple[list, list]:
    """(internal ids, unknown tokens) for S-ids such as ["S001", "s7"]."""
    by_display = {v: k for k, v in display_ids(smells).items()}
    resolved, unknown = [], []
    for token in tokens:
        match = re.fullmatch(r"[Ss]0*(\d+)", token.strip())
        key = f"S{int(match.group(1)):03d}" if match else None
        if key and key in by_display:
            if by_display[key] not in resolved:
                resolved.append(by_display[key])
        else:
            unknown.append(token)
    return resolved, unknown


def compress_ids(ids: list, limit: int = 8) -> str:
    """"S001–S003, S005" — consecutive runs collapsed, long lists truncated."""
    numbers = sorted({int(i[1:]) for i in ids if re.fullmatch(r"S\d+", i)})
    runs, start, prev = [], None, None
    for n in numbers:
        if start is None:
            start = prev = n
        elif n == prev + 1:
            prev = n
        else:
            runs.append((start, prev))
            start = prev = n
    if start is not None:
        runs.append((start, prev))
    parts = [f"S{a:03d}" if a == b else f"S{a:03d}–S{b:03d}" for a, b in runs]
    if len(parts) > limit:
        return ", ".join(parts[:limit]) + f", … ({len(numbers)} total)"
    return ", ".join(parts)


# ─── Verified locations ─────────────────────────────────────────────────────

def _symbol_patterns(smell: dict) -> list:
    where = smell.get("location") or {}
    smell_type = smell.get("type")
    patterns = []
    method = where.get("method") if smell_type in METHOD_LEVEL_SMELLS else None
    klass = (where.get("class") or smell.get("entity")) if smell_type in CLASS_LEVEL_SMELLS else None
    if method:
        name = re.escape(str(method).split(".")[-1])
        patterns.append(re.compile(rf"\b(?:def|async\s+def)\s+{name}\s*\(|\b{name}\s*\("))
    if klass:
        name = re.escape(str(klass).split(".")[-1])
        patterns.append(re.compile(rf"\b(?:class|struct|interface|enum)\s+{name}\b"))
    return patterns


def _locate(lines: list, start: int, matcher) -> int | None:
    """The reported line if it matches, else the single match within the window."""
    if 1 <= start <= len(lines) and matcher(lines[start - 1]):
        return start
    near = [n for n in range(max(1, start - SEARCH_WINDOW), min(len(lines), start + SEARCH_WINDOW) + 1)
            if matcher(lines[n - 1])]
    return near[0] if len(near) == 1 else None


def verify_location(smell: dict, lines: list) -> dict:
    """{start, end} for an annotation, or {unmapped: reason}."""
    span = (smell.get("location") or {}).get("lines") or []
    start = span[0] if span else None
    end = span[1] if len(span) > 1 else None
    if not isinstance(start, int) or isinstance(start, bool) or start < 1:
        start = smell.get("line")
    if not isinstance(start, int) or isinstance(start, bool) or start < 1:
        return {"unmapped": "no source line in the CUQA finding (file-level finding)"}
    if start > len(lines):
        return {"unmapped": f"line {start} is beyond the end of the file ({len(lines)} lines)"}
    if not isinstance(end, int) or isinstance(end, bool) or end < start:
        end = start
    length = end - start

    if smell.get("type") == "MagicNumber":
        literal = _MAGIC_RE.search(smell.get("message") or "")
        if literal:
            token = re.compile(rf"(?<![\w.]){re.escape(literal.group(1))}(?![\w.])")
            found = _locate(lines, start, lambda text: bool(token.search(text)))
            if found is None:
                return {"unmapped": f"literal {literal.group(1)} not found at line {start}"}
            return {"start": found, "end": found}
        return {"start": start, "end": start}

    patterns = _symbol_patterns(smell)
    if patterns:
        found = _locate(lines, start, lambda text: any(p.search(text) for p in patterns))
        if found is None:
            return {"unmapped": f"declaration not found near line {start}"}
        start = found
    return {"start": start, "end": min(len(lines), start + length)}


def verify_locations_in_snapshot(zip_path, top_dir: str, smells: list) -> dict:
    """Annotation locations checked against the files of the analysed commit.

    Returns {"locations": {smell id: {start,end}|{unmapped}}, "line_counts":
    {path: n}}. Reads only files that have findings, from the archive DIWO
    downloaded for this exact commit — never from a shared workspace.
    """
    locations, line_counts = {}, {}
    by_file = {}
    for smell in smells:
        path = (smell.get("location") or {}).get("file") or smell.get("relative_path")
        by_file.setdefault(path, []).append(smell)

    with zipfile.ZipFile(zip_path) as archive:
        members = {m.filename: m for m in archive.infolist()}
        for path, file_smells in by_file.items():
            member = members.get(f"{top_dir}/{path}") if path else None
            safe = path and ".." not in PurePosixPath(path).parts and not path.startswith("/")
            if member is None or not safe:
                for smell in file_smells:
                    locations[smell["id"]] = {"unmapped": "file not found in the analysed commit"}
                continue
            if member.file_size > MAX_SOURCE_BYTES_FOR_VERIFICATION:
                for smell in file_smells:
                    locations[smell["id"]] = {"unmapped": "file too large to verify"}
                continue
            lines = archive.read(member).decode("utf-8", errors="replace").splitlines()
            line_counts[path] = len(lines)
            for smell in file_smells:
                locations[smell["id"]] = verify_location(smell, lines)
    return {"locations": locations, "line_counts": line_counts}


# ─── Grouping ───────────────────────────────────────────────────────────────

def _rank(severity) -> int:
    return SEVERITY_RANK.get(severity, 3)


def group_findings(smells: list) -> list:
    """[{file, counts, groups:[{type, count, severity, entities, lines, ids}]}]

    Files and groups ordered by highest severity, then by size.
    """
    ids = display_ids(smells)
    files = {}
    for smell in smells:
        path = (smell.get("location") or {}).get("file") or smell.get("relative_path") or "unknown"
        entry = files.setdefault(path, {"file": path, "counts": {"high": 0, "medium": 0, "low": 0},
                                        "groups": {}})
        severity = smell.get("severity") or "low"
        entry["counts"][severity] = entry["counts"].get(severity, 0) + 1
        group = entry["groups"].setdefault(smell.get("type") or "Unknown", {
            "type": smell.get("type") or "Unknown", "count": 0, "severity": severity,
            "entities": [], "lines": [], "ids": [], "message": smell.get("message") or ""})
        group["count"] += 1
        if _rank(severity) < _rank(group["severity"]):
            group["severity"] = severity
        where = smell.get("location") or {}
        entity = where.get("method") or where.get("class") or smell.get("entity")
        if entity and entity not in group["entities"]:
            group["entities"].append(entity)
        if isinstance(smell.get("line"), int) and smell["line"] > 0:
            group["lines"].append(smell["line"])
        group["ids"].append(ids[smell["id"]])

    result = []
    for entry in files.values():
        groups = sorted(entry["groups"].values(),
                        key=lambda g: (_rank(g["severity"]), -g["count"], g["type"]))
        entry["groups"] = groups
        entry["top"] = min((_rank(g["severity"]) for g in groups), default=3)
        result.append(entry)
    return sorted(result, key=lambda f: (f["top"], -sum(f["counts"].values()), f["file"]))


def describe_lines(lines: list, limit: int = 5) -> str:
    unique = sorted(set(lines))
    if not unique:
        return "—"
    shown = ", ".join(str(n) for n in unique[:limit])
    return shown + (f", … (+{len(unique) - limit})" if len(unique) > limit else "")
