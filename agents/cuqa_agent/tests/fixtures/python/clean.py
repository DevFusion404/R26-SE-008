"""A clean Python module with no code smells — used as a baseline."""


def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


def subtract(a: int, b: int) -> int:
    """Subtract b from a."""
    return a - b


class Calculator:
    """Simple, clean calculator class."""

    def multiply(self, a: int, b: int) -> int:
        return a * b

    def divide(self, a: float, b: float) -> float:
        if b == 0:
            raise ValueError("Cannot divide by zero")
        return a / b
