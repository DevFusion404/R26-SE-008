"""
Code smell taxonomy
===================
R26-SE-008 | Bandara S M Y M | IT22277886

Groups a workflow's smells the two ways Stage 1 lets the developer work:

    by CATEGORY   Bloaters, Couplers, Change Preventers, …   (CUQA's taxonomy)
    by TYPE       LongMethod, FeatureEnvy, DuplicateCode, …  (deduplicated)

Why this lives in the orchestrator
----------------------------------
CUQA owns the taxonomy — it stamps every smell with `category` and
`category_priority` from its own SMELL_CATEGORY_MAP, and publishes a
`code_smell_overview` block. This module does NOT re-derive that; it reads what
CUQA stamped and rolls it up over the smells THIS WORKFLOW is holding.

That distinction matters. The overview CUQA ships describes the repository as
analysed. Stage 1 needs the same shape over the workflow's own smell list —
which is what the developer's selection resolves against, and which is what
survives a re-analysis or a filtered re-entry. Serving the CUQA block directly
would show counts that quietly disagree with the checkboxes underneath them.

It also keeps the browser out of CUQA. Stage 1 asks the orchestrator, the
orchestrator answers from the workflow it already stores, and there is one
place where "which smells exist" is decided.

Deduplication
-------------
`by_type` lists each smell type ONCE with its occurrence count and the ids
behind it, rather than repeating the type per occurrence. The type list is the
thing a developer scans to decide "I want to deal with all the Long Methods",
and a list that repeats `LongMethod` forty times cannot be scanned at all.

Pure functions: smells in, rollup out. No database, no HTTP.
"""

from collections import Counter

__all__ = [
    "UNCATEGORIZED", "CATEGORY_ORDER", "CATEGORY_PRIORITY",
    "category_of", "group_by_type", "group_by_category", "category_catalog",
    "build_taxonomy",
]

UNCATEGORIZED = "Uncategorized"

#: Display order, mirroring CUQA's _CATEGORY_ORDER so the two UIs agree.
CATEGORY_ORDER = [
    "Bloaters",
    "Object-Orientation Abusers",
    "Change Preventers",
    "Dispensables",
    "Couplers",
    "Security / Language-Specific",
    UNCATEGORIZED,
]

#: Priority per category, for a category that arrives without one.
CATEGORY_PRIORITY = {
    "Bloaters": "critical",
    "Object-Orientation Abusers": "medium",
    "Change Preventers": "critical",
    "Dispensables": "low",
    "Couplers": "medium",
    "Security / Language-Specific": "critical",
    UNCATEGORIZED: "low",
}

#: Fallback map, used ONLY for a smell CUQA did not stamp — a report from an
#: older CUQA build, or the bundled sample data. CUQA's own map is the
#: authority; this exists so Stage 1 can still group rather than dumping every
#: finding into "Uncategorized". Keep it in step with
#: cuqa_agent/src/report_generator.py::SMELL_CATEGORY_MAP.
FALLBACK_CATEGORY = {
    "LongMethod": "Bloaters",
    "LongFunction": "Bloaters",
    "LargeClass": "Bloaters",
    "TooManyParameters": "Bloaters",
    "PrimitiveObsession": "Bloaters",
    "DataClumps": "Bloaters",
    "SwitchStatements": "Object-Orientation Abusers",
    "RefusedBequest": "Object-Orientation Abusers",
    "TemporaryField": "Object-Orientation Abusers",
    "AlternativeClassesWithDifferentInterfaces": "Object-Orientation Abusers",
    "DuplicateCode": "Change Preventers",
    "DivergentChange": "Change Preventers",
    "ShotgunSurgery": "Change Preventers",
    "ParallelInheritanceHierarchies": "Change Preventers",
    "DeadCode": "Dispensables",
    "UnreachableCode": "Dispensables",
    "UnusedVariable": "Dispensables",
    "LazyClass": "Dispensables",
    "Comments": "Dispensables",
    "SpeculativeGenerality": "Dispensables",
    "DataClass": "Dispensables",
    "FeatureEnvy": "Couplers",
    "InappropriateIntimacy": "Couplers",
    "MessageChains": "Couplers",
    "MiddleMan": "Couplers",
    "UnsafeFunctionUsage": "Security / Language-Specific",
    "DeepNesting": "Security / Language-Specific",
    "GlobalVariable": "Security / Language-Specific",
    "LargeHeaderFile": "Security / Language-Specific",
    "BareExcept": "Security / Language-Specific",
    "MagicNumber": "Security / Language-Specific",
}

_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2, "unknown": 3}


def _file_of(smell):
    location = smell.get("location") or {}
    return (location.get("file")
            or smell.get("relative_path")
            or smell.get("file")
            or "(unknown file)")


def category_of(smell):
    """The category CUQA stamped, or the fallback map, or Uncategorized.

    Never invents a new category name: an unmapped type lands in
    `Uncategorized`, which is a real bucket in CUQA's own taxonomy rather than
    a label made up here.
    """
    if not isinstance(smell, dict):
        return UNCATEGORIZED
    stamped = smell.get("category")
    if isinstance(stamped, str) and stamped.strip():
        return stamped
    return FALLBACK_CATEGORY.get(smell.get("type"), UNCATEGORIZED)


def _priority_of(smell, category):
    stamped = smell.get("category_priority")
    if isinstance(stamped, str) and stamped.strip():
        return stamped
    return CATEGORY_PRIORITY.get(category, "low")


def group_by_type(smells):
    """One row per smell TYPE, with the occurrences behind it.

    The point of this shape is that a type appears once. Each row carries the
    ids it covers so the frontend can tick a whole type without re-deriving
    which smells that means — the same ids the selection endpoints resolve.
    """
    buckets = {}

    for smell in smells or []:
        if not isinstance(smell, dict):
            continue
        smell_type = smell.get("type") or "Unknown"
        category = category_of(smell)
        severity = (smell.get("severity") or "unknown").lower()

        bucket = buckets.setdefault(smell_type, {
            "type": smell_type,
            "category": category,
            "category_priority": _priority_of(smell, category),
            "count": 0,
            "severities": Counter(),
            "files": set(),
            "smell_ids": [],
        })
        bucket["count"] += 1
        bucket["severities"][severity] += 1
        bucket["files"].add(_file_of(smell))
        if smell.get("id"):
            bucket["smell_ids"].append(smell["id"])

    rows = []
    for bucket in buckets.values():
        severities = dict(bucket["severities"])
        # The worst severity present drives the row's colour and its sort
        # position: a type with one high finding is not a "low" row.
        worst = min(severities, key=lambda s: _SEVERITY_ORDER.get(s, 99), default="unknown")
        rows.append({
            "type": bucket["type"],
            "category": bucket["category"],
            "category_priority": bucket["category_priority"],
            "count": bucket["count"],
            "severities": severities,
            "worst_severity": worst,
            "file_count": len(bucket["files"]),
            "files": sorted(bucket["files"]),
            "smell_ids": bucket["smell_ids"],
        })

    rows.sort(key=lambda r: (_SEVERITY_ORDER.get(r["worst_severity"], 99), -r["count"], r["type"]))
    return rows


def group_by_category(smells):
    """One row per CATEGORY, holding its deduplicated type rows.

    Categories with no findings are omitted: this list is the WORKLIST, and an
    empty category is a row the developer cannot act on. `category_catalog`
    below is the complete list, for the overview bar that reports what the
    repository does and does not have.
    """
    by_type = group_by_type(smells)

    buckets = {}
    for row in by_type:
        bucket = buckets.setdefault(row["category"], {
            "category": row["category"],
            "priority": row["category_priority"],
            "count": 0,
            "types": [],
            "severities": Counter(),
            "files": set(),
            "smell_ids": [],
        })
        bucket["count"] += row["count"]
        bucket["types"].append(row)
        bucket["severities"].update(row["severities"])
        bucket["files"].update(row["files"])
        bucket["smell_ids"].extend(row["smell_ids"])

    ordered = []
    for name in CATEGORY_ORDER:
        if name in buckets:
            ordered.append(buckets.pop(name))
    # Anything CUQA produced that this order does not know about still ships,
    # after the known ones, rather than being dropped.
    ordered.extend(buckets[name] for name in sorted(buckets))

    return [
        {
            "category": b["category"],
            "priority": b["priority"],
            "count": b["count"],
            "type_count": len(b["types"]),
            "severities": dict(b["severities"]),
            "file_count": len(b["files"]),
            "types": b["types"],
            "smell_ids": b["smell_ids"],
        }
        for b in ordered
    ]


def category_catalog(by_category):
    """Every category in CUQA's taxonomy, whether this workflow has one or not.

    The overview bar answers "what KIND of problem does this repository have",
    and a bar that lists only the categories with findings cannot answer the
    other half of that question. Six chips of which four read zero says "this
    repository has Bloaters and Magic Numbers and nothing else"; two chips say
    only "here are two categories", and leave the developer to remember which
    of CUQA's six they are not looking at.

    So the ZERO ROWS ARE THE POINT — they are evidence, not padding.

    `count`, `type_count` and `file_count` come from the worklist rows when the
    category is present and are a true 0 when it is not. `present` says which,
    so the UI never has to infer it from a count that could legitimately be 0.

    Order follows CATEGORY_ORDER, which mirrors CUQA's own, and any category
    CUQA produced that this order does not know about still ships, after the
    known ones — the same rule `group_by_category` uses, so the bar and the
    accordion list categories in the same sequence.
    """
    present = {row["category"]: row for row in (by_category or [])}

    def entry(name, row):
        return {
            "category": name,
            "priority": (row or {}).get("priority") or CATEGORY_PRIORITY.get(name, "low"),
            "count": (row or {}).get("count", 0),
            "type_count": (row or {}).get("type_count", 0),
            "file_count": (row or {}).get("file_count", 0),
            "present": row is not None,
        }

    catalog = [entry(name, present.pop(name, None)) for name in CATEGORY_ORDER]
    catalog.extend(entry(name, present[name]) for name in sorted(present))
    return catalog


def build_taxonomy(smells):
    """The whole Stage 1 grouping payload for one workflow."""
    smells = [s for s in (smells or []) if isinstance(s, dict)]
    by_category = group_by_category(smells)
    by_type = group_by_type(smells)

    return {
        "total_smells": len(smells),
        "type_count": len(by_type),
        # Categories the workflow ACTUALLY has. The header reports this, so
        # "2 categories" stays a true statement about the repository even
        # though the bar below it shows all seven chips.
        "category_count": len(by_category),
        "categories": by_category,
        "catalog": category_catalog(by_category),
        "types": by_type,
        "severities": dict(Counter(
            (s.get("severity") or "unknown").lower() for s in smells
        )),
    }
