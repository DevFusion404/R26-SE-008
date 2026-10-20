# javalang.ast is dynamically typed; see __init__.pyi.
from typing import Any

def __getattr__(name: str) -> Any: ...
