"""
test_smell_taxonomy.py
======================
R26-SE-008 | Bandara S M Y M | IT22277886

The category rollup Stage 1's overview bar renders.

The distinction under test is between the two lists `build_taxonomy` returns:

    categories   the WORKLIST — only what this repository actually has
    catalog      the COMPLETE taxonomy — every category, zeroes included

They are different answers to different questions, and the bug worth guarding
against is one silently becoming the other: a catalog that dropped its zeroes
would stop reporting what the repository does NOT have, and a worklist that
gained them would put un-actionable rows in the accordion.
"""

import pytest

from domain.smell_taxonomy import (
    CATEGORY_ORDER,
    CATEGORY_PRIORITY,
    build_taxonomy,
    category_catalog,
    group_by_category,
)


def smell(type_, category, file, line, severity="low"):
    return {
        "id": f"{file}:{line}:0",
        "type": type_,
        "severity": severity,
        "category": category,
        "location": {"file": file},
    }


SECURITY = "Security / Language-Specific"

SMELLS = [
    smell("MagicNumber", SECURITY, "src/Pricing.java", 46),
    smell("MagicNumber", SECURITY, "src/Pricing.java", 88),
    smell("LargeHeaderFile", SECURITY, "include/legacy_api.h", 1, "medium"),
    smell("LongMethod", "Bloaters", "src/Pricing.java", 12, "high"),
]


# ── The worklist keeps its own rule ──────────────────────────────────────────

def test_worklist_lists_only_categories_with_findings():
    rows = group_by_category(SMELLS)
    assert [r["category"] for r in rows] == ["Bloaters", SECURITY]


def test_build_taxonomy_counts_only_the_categories_present():
    """The header says '2 categories' while the bar shows seven chips.

    Both are true and they answer different questions, so this number must not
    drift to len(catalog) when the catalog is added to the payload.
    """
    taxonomy = build_taxonomy(SMELLS)
    assert taxonomy["category_count"] == 2
    assert taxonomy["type_count"] == 3
    assert taxonomy["total_smells"] == 4


# ── The catalog is complete ──────────────────────────────────────────────────

def test_catalog_lists_every_category_in_the_taxonomy():
    catalog = category_catalog(group_by_category(SMELLS))
    assert [c["category"] for c in catalog] == CATEGORY_ORDER


def test_catalog_reports_zero_rather_than_omitting_them():
    catalog = {c["category"]: c for c in category_catalog(group_by_category(SMELLS))}

    empty = catalog["Couplers"]
    assert empty["count"] == 0
    assert empty["type_count"] == 0
    assert empty["file_count"] == 0
    assert empty["present"] is False
    # An absent category still carries its priority, so the chip can show it.
    assert empty["priority"] == CATEGORY_PRIORITY["Couplers"]


def test_catalog_carries_the_real_counts_for_categories_present():
    catalog = {c["category"]: c for c in category_catalog(group_by_category(SMELLS))}

    security = catalog[SECURITY]
    assert security["present"] is True
    assert security["count"] == 3
    assert security["type_count"] == 2         # MagicNumber + LargeHeaderFile
    assert security["file_count"] == 2

    bloaters = catalog["Bloaters"]
    assert bloaters["count"] == 1
    assert bloaters["type_count"] == 1


def test_catalog_counts_agree_with_the_worklist():
    """Two lists, one set of numbers. A chip and its accordion group must not
    disagree about how many findings a category holds."""
    rows = group_by_category(SMELLS)
    catalog = {c["category"]: c for c in category_catalog(rows)}

    for row in rows:
        entry = catalog[row["category"]]
        assert entry["count"] == row["count"]
        assert entry["type_count"] == row["type_count"]
        assert entry["file_count"] == row["file_count"]

    assert sum(c["count"] for c in catalog.values()) == len(SMELLS)


def test_catalog_ships_a_category_the_known_order_does_not_have():
    """CUQA inventing a category must not drop it off the bar."""
    rows = group_by_category(SMELLS + [smell("Whatever", "Brand New Group", "x.py", 3)])
    catalog = category_catalog(rows)

    names = [c["category"] for c in catalog]
    assert names[: len(CATEGORY_ORDER)] == CATEGORY_ORDER
    assert names[-1] == "Brand New Group"
    assert catalog[-1]["present"] is True


# ── Degenerate input ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("empty", [[], None])
def test_no_smells_still_lists_every_category_at_zero(empty):
    taxonomy = build_taxonomy(empty)

    assert taxonomy["category_count"] == 0
    assert taxonomy["categories"] == []
    assert [c["category"] for c in taxonomy["catalog"]] == CATEGORY_ORDER
    assert all(c["count"] == 0 and c["present"] is False for c in taxonomy["catalog"])


def test_catalog_of_nothing_is_still_the_full_taxonomy():
    assert [c["category"] for c in category_catalog(None)] == CATEGORY_ORDER
    assert [c["category"] for c in category_catalog([])] == CATEGORY_ORDER
