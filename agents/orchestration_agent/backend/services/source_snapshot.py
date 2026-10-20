"""
Per-workflow source snapshot
============================
R26-SE-008 | Bandara S M Y M | IT22277886

The CUQA agent holds ONE shared workspace, and every upload replaces it. On the
hosted app several developers use the same CUQA, so between one developer's
smell selection and their transformation, someone else's upload could replace
the repository the plan was made for — and the transformation then failed
with "The source of every planned file was unreadable".

The fix is to read the selected files' source ONCE, at smell selection, while
CUQA still holds the repository the developer analysed, and keep it on the
workflow row (source_snapshot_json). The transformation reads from that copy
first and only asks CUQA for a file the snapshot does not have. Whatever is
uploaded to CUQA afterwards no longer reaches a workflow already in progress.

Both halves are best effort: a snapshot that cannot be taken or stored is
logged and the workflow carries on exactly as it did before — reading CUQA
live at transformation time.
"""

import json
import logging

import config
from db.workflow_repository import log_event, now_iso, parse_json_field, update_workflow
from domain.audit_detail import smells_by_file
from services.source_service import fetch_workspace_sources

logger = logging.getLogger(__name__)


def _norm(path) -> str:
    """One spelling per file: forward slashes, no leading './' or '/'."""
    value = str(path or "").replace("\\", "/").strip()
    while value.startswith("./"):
        value = value[2:]
    return value.lstrip("/")


def capture_source_snapshot(wf_id: str, selected_smells: list) -> dict:
    """Read and store the source of every file that carries a selected smell.

    Returns a summary {files, missing, bytes, stored, truncated, error}. Never
    raises: a failure here must not block the smell selection.
    """
    paths = [row["file"] for row in smells_by_file(selected_smells) if row.get("file")]
    summary = {"files": 0, "missing": [], "bytes": 0, "stored": False,
               "truncated": [], "error": ""}
    if not paths:
        return summary

    try:
        fetched = fetch_workspace_sources(paths, timeout=config.source_fetch_timeout())
    except Exception as exc:  # noqa: BLE001 - CUQA and SCTVA both unreachable, or worse
        summary["error"] = getattr(exc, "message", None) or str(exc)
        log_event(wf_id, "smell_selection", "source_snapshot_failed",
                  {"files": len(paths), "reason": summary["error"]}, actor="system")
        return summary

    # Bounded, so one enormous selection cannot bloat the workflow row; files
    # past the limit are simply read live at transformation time.
    limit = config.source_snapshot_max_bytes()
    kept, used, truncated = [], 0, []
    for entry in fetched.get("files") or []:
        size = len((entry.get("source_code") or "").encode("utf-8"))
        if used + size > limit:
            truncated.append(entry.get("file_name"))
            continue
        kept.append(entry)
        used += size

    snapshot = {
        "captured_at": now_iso(),
        "origin": fetched.get("origin"),
        "files": kept,
        "missing": fetched.get("missing") or [],
        "truncated": truncated,
    }
    summary.update(files=len(kept), missing=snapshot["missing"], bytes=used, truncated=truncated)

    try:
        update_workflow(wf_id, source_snapshot_json=json.dumps(snapshot))
        summary["stored"] = True
    except Exception as exc:  # noqa: BLE001 - e.g. the Supabase column is not migrated yet
        summary["error"] = f"snapshot not stored: {type(exc).__name__}"
        logger.warning("Source snapshot for %s not stored: %s", wf_id, exc)

    log_event(wf_id, "smell_selection",
              "source_snapshot_saved" if summary["stored"] else "source_snapshot_failed",
              {"files": summary["files"], "missing": summary["missing"][:50],
               "bytes": used, "truncated": truncated[:50],
               **({"reason": summary["error"]} if summary["error"] else {})},
              actor="system")
    return summary


def snapshot_source_loader(wf: dict):
    """A source_loader for run_transformation() that prefers the snapshot.

    Files in the snapshot are served from it; any other requested file is read
    live from CUQA, as before. Returns None when the workflow has no snapshot,
    so the caller keeps the plain live read.
    """
    snapshot = parse_json_field(wf, "source_snapshot_json") or {}
    stored = {_norm(f.get("file_name")): f for f in snapshot.get("files") or []
              if f.get("file_name") and f.get("source_code") is not None}
    if not stored:
        return None

    def load(paths):
        requested = [str(p) for p in paths or []]
        files, live = [], []
        for path in requested:
            entry = stored.get(_norm(path))
            if entry:
                files.append({**entry, "file_name": path})
            else:
                live.append(path)

        missing, origin = [], "workflow_snapshot"
        if live:
            fetched = fetch_workspace_sources(live, timeout=config.source_fetch_timeout())
            files.extend(fetched.get("files") or [])
            missing = fetched.get("missing") or []
            origin = f"workflow_snapshot+{fetched.get('origin', 'cuqa_workspace')}"

        return {"files": files, "missing": missing, "imported": len(files),
                "total": len(requested), "origin": origin}

    return load
