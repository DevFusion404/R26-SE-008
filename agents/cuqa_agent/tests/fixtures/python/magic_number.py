"""Python fixture: MagicNumber smell — literals not in {0, 1, -1, 2, True, False}."""


def calculate_area(radius):
    return 3.14159 * radius * radius  # 3.14159 is a magic number


def get_timeout():
    return 30  # 30 is a magic number (not 0/1/-1/2)


def safe_zero():
    return 0   # NOT a magic number — in the safe set


def safe_one():
    return 1   # NOT a magic number — in the safe set


def safe_minus_one():
    return -1  # NOT a magic number — in the safe set


def safe_two():
    return 2   # NOT a magic number — in the safe set
