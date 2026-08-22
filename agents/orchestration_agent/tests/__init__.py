"""
DIWO Orchestration Agent test suite.

This package exists so the test directories do not collide with the backend's
own top-level packages. `tests/api/` and `backend/api/` would otherwise both
import as `api`, and since conftest puts backend/ on sys.path, the backend one
wins and the test module is never found.
"""
