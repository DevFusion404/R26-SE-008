"""
Workspace source text
=====================
R26-SE-008 | Bandara S M Y M | IT22277886

The CUQA report describes files but never ships their contents, and three
things downstream need the text itself: the `source_files` field of an SCTVA
execute request, the Code Smell Review source viewer, and the whole-project
archive the Results stage downloads.

CUQA OWNS THE WORKSPACE, so CUQA is asked for the text, over HTTP.

This used to go to SCTVA's POST /sctva/cuqa-sources, which locates the files by
scanning its OWN machine's temp directory for CUQA's `cuqa_*` scratch folder.
That only holds while the two agents share a filesystem. Locally they do — one
machine, one %TEMP% — so it worked all through development and could not work
in any deployed environment, where each agent is its own container: SCTVA
scanned an empty /tmp, reported every planned file missing, and Stage 3
answered 422 "SCTVA could not read the source of any planned file".

Asking CUQA holds in both, because it asks the agent that actually has the
repository instead of hoping to find its scratch directory. The filesystem scan
is kept as a FALLBACK for the one case it still serves — a co-located run where
CUQA cannot answer but its workspace is still on disk — and to fill in paths
CUQA reports missing.

Returns, from every path: {files, missing, imported, total, origin}.
"""

from clients.cuqa_client import CUQAError, cuqa_base_url, fetch_source_files
from clients.sctva_client import SCTVAError, fetch_workspace_sources as _sctva_sources

__all__ = ["fetch_workspace_sources"]


def _empty(requested):
    return {"files": [], "missing": list(requested), "imported": 0,
            "total": len(requested), "origin": "none"}


def fetch_workspace_sources(file_paths: list, timeout: int = 60) -> dict:
    """Read the source text of repo-relative paths out of the CUQA workspace.

    Raises SCTVAError only when NEITHER agent could be reached, so the two
    callers keep the error handling they already had; the status carried is the
    one that best explains the failure to the developer.
    """
    requested = [str(p) for p in (file_paths or [])]
    if not requested:
        return _empty(requested)

    cuqa_failure = None
    try:
        payload = fetch_source_files(requested, timeout=timeout)
        payload["origin"] = "cuqa_workspace"
    except CUQAError as exc:
        cuqa_failure = exc
        payload = None

    # CUQA answered. Anything it could not resolve is worth one look on a
    # shared filesystem, which is free when there is not one — SCTVA simply
    # finds no workspace — and is never allowed to turn a partial success into
    # a failure.
    if payload is not None:
        if payload["missing"]:
            try:
                salvage = _sctva_sources(payload["missing"], timeout=timeout)
            except SCTVAError:
                salvage = None
            if salvage and salvage["files"]:
                recovered = {f.get("file_name") for f in salvage["files"]}
                payload["files"].extend(salvage["files"])
                payload["missing"] = [p for p in payload["missing"] if p not in recovered]
                payload["imported"] = len(payload["files"])
                payload["origin"] = "cuqa_workspace+sctva_temp_workspace"
        return payload

    # CUQA could not be reached at all. The filesystem scan is the only thing
    # left, and it is the right thing to try when both agents are co-located.
    try:
        payload = _sctva_sources(requested, timeout=timeout)
        payload["origin"] = "sctva_temp_workspace"
        return payload
    except SCTVAError as sctva_failure:
        raise SCTVAError(
            f"The source of the requested files could not be read. "
            f"CUQA ({cuqa_base_url()}) reported: {cuqa_failure.message} "
            f"SCTVA then reported: {sctva_failure.message}",
            status=cuqa_failure.status if cuqa_failure.status != 502 else sctva_failure.status,
        ) from cuqa_failure
