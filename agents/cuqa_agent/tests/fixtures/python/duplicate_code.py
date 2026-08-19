"""Python fixture: DuplicateCode smell — two structurally identical function bodies."""


def process_alpha(x, y, z):
    result = x + y
    result = result * z
    return result


def process_beta(a, b, c):
    result = a + b
    result = result * c
    return result
# Both have structurally identical AST structure → DuplicateCode
