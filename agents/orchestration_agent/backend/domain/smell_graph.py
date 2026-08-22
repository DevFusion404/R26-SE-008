"""
Smell interaction graph
=======================
R26-SE-008 | Bandara S M Y M | IT22277886

Containment, overlap and clone relationships between smells, so the impact
panel can explain that selections are not independent.

Three relationships change the arithmetic:

  CONTAINS  (super-additive) a LargeClass whose range holds three LongMethods.
            Extracting the methods first shrinks the class, so doing both is
            worth more than the sum. Fixing only the class leaves the methods
            to be re-detected on the next CUQA run.

  OVERLAPS  (sub-additive) two smells whose line ranges intersect and whose
            refactorings both rewrite the region. SCTVA applies actions
            sequentially against line numbers, so the second action's range is
            stale once the first has edited the file. Today that surfaces as a
            mysterious Stage 4 no-op; it should surface here, at selection.

  CLONE_OF  (all-or-nothing) DuplicateCode findings come in clones. Fixing one
            and not its twin leaves duplication exactly where it was.

Pure: takes the flattened smell list from cuqa_report_to_smells(), returns
edges. No I/O, no Flask, no database.
"""

from typing import Optional

CONTAINS = "contains"
OVERLAPS = "overlaps"
CLONE_OF = "clone_of"

#: Smells that describe a class/file rather than a single member. Kept local
#: rather than imported from cuqa_normalizer.CLASS_LEVEL_SMELLS because the
#: question here is "does this enclose other findings", which is a slightly
#: different one from "what does its `entity` name".
CLASS_LEVEL = {
    "LargeClass", "GodClass", "LazyClass", "PrimitiveObsession",
    "InappropriateIntimacy", "SpeculativeGenerality", "LargeHeaderFile",
    "Large Class", "God Class", "Lazy Class", "Primitive Obsession",
    "Speculative Generality",
}

#: Pairwise edge building is O(n²) within a file. Files with more findings than
#: this get their edges skipped rather than stalling the request — the panel
#: degrades, it does not hang.
MAX_SMELLS_PER_FILE = 120

__all__ = ["CONTAINS", "OVERLAPS", "CLONE_OF", "build_edges", "selection_notes"]


def _span(smell):
    """The line range a smell occupies, mirroring the source viewer's rules."""
    lines = (smell.get("location") or {}).get("lines") or []
    if len(lines) < 2:
        line = smell.get("line") or 0
        return line, line
    return lines[0] or 0, lines[1] or 0


def build_edges(smells: Optional[list]) -> list:
    """All pairwise relationships worth telling the developer about."""
    edges = []

    by_file = {}
    for smell in smells or []:
        path = (smell.get("location") or {}).get("file") or smell.get("relative_path")
        by_file.setdefault(path, []).append(smell)

    for group in by_file.values():
        if len(group) > MAX_SMELLS_PER_FILE:
            continue

        for index, a in enumerate(group):
            a_start, a_end = _span(a)
            if a_end < a_start:
                continue

            for b in group[index + 1:]:
                b_start, b_end = _span(b)
                if a_end < b_start or b_end < a_start:
                    continue                                   # disjoint

                a_class = a.get("type") in CLASS_LEVEL
                b_class = b.get("type") in CLASS_LEVEL

                if a_class and not b_class:
                    edges.append({
                        "type": CONTAINS, "from": a["id"], "to": b["id"],
                        "note": f"{b.get('type')} sits inside this {a.get('type')}",
                    })
                elif b_class and not a_class:
                    edges.append({
                        "type": CONTAINS, "from": b["id"], "to": a["id"],
                        "note": f"{a.get('type')} sits inside this {b.get('type')}",
                    })
                else:
                    edges.append({
                        "type": OVERLAPS, "from": a["id"], "to": b["id"],
                        "note": (
                            f"Both rewrite lines {max(a_start, b_start)}–"
                            f"{min(a_end, b_end)}; applying one shifts the other's "
                            "line numbers, so the second may no-op."
                        ),
                    })

    # Duplicate-code clone groups, which span files.
    clones = [s for s in (smells or []) if s.get("type") in ("DuplicateCode", "Duplicate Code")]
    for index, a in enumerate(clones):
        for b in clones[index + 1:]:
            edges.append({
                "type": CLONE_OF, "from": a["id"], "to": b["id"],
                "note": "Clone pair — fixing one alone leaves the duplication in place.",
            })

    return edges


def selection_notes(edges: list, selected_ids) -> list:
    """Turn edges into advice about the CURRENT selection.

    Only edges whose endpoints are actually selected (or pointedly not) produce
    a note — the developer is told about consequences of the choice in front of
    them, not handed the whole graph.
    """
    selected = set(selected_ids or [])
    notes = []
    seen = set()

    def add(level, ids, message):
        key = (level, tuple(sorted(ids)), message)
        if key not in seen:
            seen.add(key)
            notes.append({"level": level, "smell_ids": list(ids), "message": message})

    # Containment notes are collapsed per container. Selecting three methods
    # inside one unselected class is ONE fact about that class, not three
    # identical sentences — repeating it adds no information and buries the
    # notes that differ.
    unselected_containers = {}

    for edge in edges or []:
        a_in = edge["from"] in selected
        b_in = edge["to"] in selected

        if edge["type"] == OVERLAPS and a_in and b_in:
            add("warning", [edge["from"], edge["to"]], f"Ordering conflict. {edge['note']}")

        elif edge["type"] == CONTAINS and b_in and not a_in:
            unselected_containers.setdefault(edge["from"], []).append(edge["to"])

        elif edge["type"] == CLONE_OF and (a_in != b_in):
            add("warning", [edge["from"], edge["to"]],
                "Only one clone of this block is selected. Duplication will not "
                "drop until all clones are fixed.")

    for container, members in unselected_containers.items():
        count = len(members)
        add("info", [container, *members],
            f"{count} selected smell{'s' if count > 1 else ''} "
            f"{'sit' if count > 1 else 'sits'} inside a class-level smell that is "
            "not selected — the container stays flagged after this run.")

    return notes
