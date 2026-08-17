"""Python fixture: SwitchStatements — 4 elif branches (threshold >= 4)."""


def classify(value):
    if value == "a":
        return 1
    elif value == "b":
        return 2
    elif value == "c":
        return 3
    elif value == "d":
        return 4
    elif value == "e":
        return 5
    # 4+ elif branches → SwitchStatements triggered
    return 0
