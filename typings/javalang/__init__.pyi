# Type stub for javalang (editor / type-checker only; Python never loads .pyi files).
#
# javalang ships no type information and its tree nodes receive their
# attributes (condition, member, declarators, ...) at runtime, so Pylance /
# pyright reported every attribute access as unknown. These stubs declare the
# library as dynamically typed: every name in it is Any.
from typing import Any

def __getattr__(name: str) -> Any: ...
