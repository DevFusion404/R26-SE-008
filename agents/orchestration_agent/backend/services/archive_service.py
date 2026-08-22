"""
Refactored-source archive assembly
==================================
R26-SE-008 | Bandara S M Y M | IT22277886

Builds and stores the ZIP the developer downloads at the end of a session.

Whole-project behaviour, preserved exactly:
    accepted transformations  -> the refactored version
    rejected transformations  -> the original version they were reverted to
    unchanged files           -> the original version
The caller hands over the final source of each file, so a rejected file
arrives already holding its original text and lands in the archive that way.

Moved out of diwo/routes.py; the only change is that the output directory and
the size guards now come from config (runtime/archives/ instead of
reports/archives/).
"""

import io
import json
import zipfile
from pathlib import Path

from config import MAX_ARCHIVE_FILES, MAX_ARCHIVE_BYTES, archives_dir
from db.workflow_repository import now_iso


def archive_path(wf_id: str) -> Path:
    """One archive per workflow; a new accept overwrites the previous one.

    The id is sanitised even though every caller passes a server-generated
    `wf_<uuid>`: this function turns a caller-supplied string into a
    filesystem path, and a helper that is safe only because of where it
    happens to be called from is one refactor away from not being. Traversal
    segments are stripped and the result is flattened to a single name, so the
    archive can only ever land inside archives_dir().
    """
    safe = safe_archive_path(wf_id, "workflow").replace("/", "_")
    return archives_dir() / f"{safe}.zip"


def safe_archive_path(value, fallback: str = "file") -> str:
    """Normalize a repo-relative path so extraction cannot escape its folder.

    Drops drive letters, leading slashes and every '..' segment, and keeps the
    remaining folders so 'src/util/Helper.java' extracts back into src/util/.
    """
    text = str(value or "").replace("\\", "/")
    if len(text) > 1 and text[1] == ":":
        text = text[2:]
    parts = [p for p in text.split("/") if p and p not in (".", "..")]
    return "/".join(parts) or fallback


def build_refactored_archive(wf_id: str, files: list, meta: dict):
    """Zip the final source of each file, preserving its folder structure.

    `files` is a list of {path, content, state} objects — `content` is already
    the code the developer settled on, so a rejected file arrives holding its
    original source and lands in the archive that way.

    Returns (bytes, manifest) or raises ValueError with a reportable message.
    """
    if not isinstance(files, list):
        raise ValueError("'files' must be a list of {path, content} objects.")
    if len(files) > MAX_ARCHIVE_FILES:
        raise ValueError(f"Too many files for one archive (limit {MAX_ARCHIVE_FILES}).")

    entries = []
    used_names = set()
    total_bytes = 0

    for index, item in enumerate(files, start=1):
        if not isinstance(item, dict):
            continue

        content = item.get("content")
        if content is None:
            content = item.get("after") or item.get("refactored_code") or ""
        if not isinstance(content, str) or content == "":
            continue

        total_bytes += len(content.encode("utf-8"))
        if total_bytes > MAX_ARCHIVE_BYTES:
            raise ValueError(f"Archive exceeds the {MAX_ARCHIVE_BYTES // (1024 * 1024)} MB limit.")

        name = safe_archive_path(
            item.get("path") or item.get("file") or item.get("relative_path"),
            f"file-{index}",
        )

        # Two entries cannot share a name or the archive silently loses one.
        if name in used_names:
            stem, dot, ext = name.rpartition(".")
            base, suffix = (stem, f".{ext}") if dot else (name, "")
            counter = 2
            while f"{base}({counter}){suffix}" in used_names:
                counter += 1
            name = f"{base}({counter}){suffix}"
        used_names.add(name)

        entries.append({
            "path": name,
            "content": content,
            "state": item.get("state") or ("reverted_to_original"
                                           if item.get("decision") == "reject" else "refactored"),
        })

    if not entries:
        raise ValueError("None of the supplied files carried any content to archive.")

    manifest = {
        "workflow_id": wf_id,
        "generated_at": now_iso(),
        **{k: v for k, v in (meta or {}).items() if v is not None},
        "files": [{"path": e["path"], "state": e["state"]} for e in entries],
        "file_count": len(entries),
    }

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for entry in entries:
            archive.writestr(entry["path"], entry["content"])
        archive.writestr("REFACTORING_MANIFEST.json", json.dumps(manifest, indent=2))

    return buffer.getvalue(), manifest


def store_refactored_archive(wf_id: str, files: list, meta: dict):
    """Build the archive and keep it on disk so it can be downloaded later."""
    payload, manifest = build_refactored_archive(wf_id, files, meta)
    target = archive_path(wf_id)
    target.write_bytes(payload)

    return payload, {
        "filename": f"diwo_refactored_{wf_id}.zip",
        "file_count": manifest["file_count"],
        "bytes": len(payload),
        "generated_at": manifest["generated_at"],
        "url": f"/api/workflows/{wf_id}/refactored-archive",
        "files": manifest["files"],
    }
