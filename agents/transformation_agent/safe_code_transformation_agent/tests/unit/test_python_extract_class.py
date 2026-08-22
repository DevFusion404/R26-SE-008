import ast
import inspect

from sctva.transformers.python_extract_class import apply_extract_class


LIBRARY_SOURCE = '''class LibraryManager:
    def __init__(self):
        self.books = {}
        self.members = {}
        self.loans = []
        self.fines = {}

    def add_book(self, code, title): self.books[code] = {"title": title, "available": True}
    def register_member(self, mid, name): self.members[mid] = name
    def issue_book(self, code, mid):
        if code in self.books and mid in self.members and self.books[code]["available"]:
            self.books[code]["available"] = False
            self.loans.append((code, mid))
            return True
        return False
    def return_book(self, code, mid):
        if (code, mid) in self.loans:
            self.loans.remove((code, mid)); self.books[code]["available"] = True; return True
        return False
    def count_loans(self): return len(self.loans)
    def fine_balance(self, mid): return self.fines.get(mid, 0)
'''


def _library_behavior(source: str) -> tuple[bool, int, bool, int]:
    namespace: dict[str, object] = {}
    exec(source, namespace)
    manager = namespace["LibraryManager"]()
    manager.add_book("B1", "Python")
    manager.register_member("M1", "Nimal")
    issued = manager.issue_book("B1", "M1")
    count_after_issue = manager.count_loans()
    returned = manager.return_book("B1", "M1")
    return issued, count_after_issue, returned, manager.count_loans()


def test_extract_class_supports_compact_methods_and_preserves_behavior():
    transformed, replacements, metadata = apply_extract_class(
        LIBRARY_SOURCE,
        source_class="LibraryManager",
        new_class_name="LoanManager",
        methods_to_extract=["issue_book", "return_book", "count_loans"],
        fields_to_extract=["books", "members", "loans"],
    )

    ast.parse(transformed)
    assert replacements == 1
    assert metadata["status"] == "success"
    assert metadata["smell_reduced"] is True
    assert metadata["validation"]["syntax"] == "PASS"
    assert "class LoanManager:" in transformed
    assert "def count_loans(self): return len(self.loans)" in transformed
    assert metadata["compatibility"]["use_member_descriptors"] is True
    assert metadata["compatibility"]["descriptor_methods"] == [
        "count_loans",
        "issue_book",
        "return_book",
    ]
    assert "def __getattr__(self, name):" not in transformed
    assert _library_behavior(transformed) == _library_behavior(LIBRARY_SOURCE)


def test_extract_class_supports_multiline_method_signatures():
    source = '''class AccountManager:
    def __init__(self):
        self.entries = []
        self.enabled = True

    def add_entry(
        self,
        name: str,
        amount: int = 0,
    ) -> None:
        self.entries.append((name, amount))

    def entry_count(self): return len(self.entries)
    def is_enabled(self): return self.enabled
    def disable(self): self.enabled = False
'''

    transformed, replacements, metadata = apply_extract_class(
        source,
        source_class="AccountManager",
        new_class_name="EntryManager",
        methods_to_extract=["add_entry", "entry_count"],
        fields_to_extract=["entries"],
    )

    ast.parse(transformed)
    assert replacements == 1
    assert metadata["status"] == "success"
    assert "class EntryManager:" in transformed
    assert metadata["compatibility"]["descriptor_methods"] == ["add_entry", "entry_count"]


def _extract_with_complete_repository(
    source: str,
    *,
    source_class: str,
    new_class_name: str,
    methods: list[str],
    fields: list[str],
    extra_files: list[dict[str, str]] | None = None,
    required_public_fields: list[str] | None = None,
):
    files = [
        {
            "file_name": "manager.py",
            "source_code": source,
            "language": "python",
        },
        *(extra_files or []),
    ]
    return apply_extract_class(
        source,
        source_file="manager.py",
        current_file_name="manager.py",
        source_class=source_class,
        new_class_name=new_class_name,
        methods_to_extract=methods,
        fields_to_extract=fields,
        project_source_files=files,
        repository_complete=True,
        required_public_fields=required_public_fields,
    )


def test_complete_repository_preserves_public_surface_without_wrappers_or_properties():
    transformed, replacements, metadata = _extract_with_complete_repository(
        LIBRARY_SOURCE,
        source_class="LibraryManager",
        new_class_name="LoanManager",
        methods=["issue_book", "return_book", "count_loans"],
        fields=["books", "members", "loans"],
    )

    assert replacements == 1
    assert metadata["compatibility"]["delegated_methods"] == []
    assert metadata["compatibility"]["property_fields"] == []
    assert metadata["compatibility"]["descriptor_methods"] == [
        "count_loans",
        "issue_book",
        "return_book",
    ]
    assert metadata["compatibility"]["descriptor_fields"] == ["books", "loans", "members"]
    source_class = next(
        node for node in ast.parse(transformed).body
        if isinstance(node, ast.ClassDef) and node.name == "LibraryManager"
    )
    source_methods = {node.name for node in source_class.body if isinstance(node, ast.FunctionDef)}
    assert "issue_book" not in source_methods
    assert "books" not in source_methods


def test_external_method_caller_keeps_complete_public_method_surface():
    caller = '''from manager import LibraryManager
manager = LibraryManager()
manager.issue_book("B1", "M1")
'''
    transformed, replacements, metadata = _extract_with_complete_repository(
        LIBRARY_SOURCE,
        source_class="LibraryManager",
        new_class_name="LoanManager",
        methods=["issue_book", "return_book", "count_loans"],
        fields=["books", "members", "loans"],
        extra_files=[{"file_name": "caller.py", "source_code": caller, "language": "python"}],
    )

    assert replacements == 1
    assert metadata["compatibility"]["delegated_methods"] == []
    assert metadata["compatibility"]["descriptor_methods"] == [
        "count_loans",
        "issue_book",
        "return_book",
    ]
    assert "def __getattr__(self, name):" not in transformed
    assert "return self._loan_manager.return_book(code, mid)" not in transformed


def test_external_read_only_field_access_uses_state_descriptor():
    caller = '''from manager import LibraryManager
manager = LibraryManager()
print(manager.loans)
'''
    transformed, replacements, metadata = _extract_with_complete_repository(
        LIBRARY_SOURCE,
        source_class="LibraryManager",
        new_class_name="LoanManager",
        methods=["issue_book", "return_book", "count_loans"],
        fields=["books", "members", "loans"],
        extra_files=[{"file_name": "caller.py", "source_code": caller, "language": "python"}],
    )

    assert replacements == 1
    assert metadata["compatibility"]["property_fields"] == []
    assert "loans" in metadata["compatibility"]["descriptor_fields"]
    assert metadata["compatibility"]["writable_property_fields"] == []
    assert "def __getattr__(self, name):" not in transformed
    assert "@loans.setter" not in transformed


def test_external_field_write_updates_helper_without_duplicate_state():
    caller = '''from manager import LibraryManager
manager = LibraryManager()
manager.loans = []
'''
    transformed, replacements, metadata = _extract_with_complete_repository(
        LIBRARY_SOURCE,
        source_class="LibraryManager",
        new_class_name="LoanManager",
        methods=["issue_book", "return_book", "count_loans"],
        fields=["books", "members", "loans"],
        extra_files=[{"file_name": "caller.py", "source_code": caller, "language": "python"}],
    )

    assert replacements == 1
    namespace: dict[str, object] = {}
    exec(transformed, namespace)
    manager = namespace["LibraryManager"]()
    replacement = [("B1", "M1")]
    manager.loans = replacement

    assert metadata["compatibility"]["descriptor_fields"] == ["books", "loans", "members"]
    assert metadata["validation"]["state_compatibility"] == "PASS"
    assert manager.loans is replacement
    assert manager._loan_manager.loans is replacement
    assert "loans" not in vars(manager)
    del manager.loans
    assert not hasattr(manager._loan_manager, "loans")
    assert "loans" not in vars(manager)


def test_shared_field_used_by_remaining_method_is_redirected_without_property():
    source = '''class RecordManager:
    def __init__(self):
        self.records = []
        self.enabled = True

    def add_record(self, value): self.records.append(value)
    def remove_record(self, value): self.records.remove(value)
    def record_count(self): return len(self.records)
    def summary(self): return {"records": len(self.records), "enabled": self.enabled}
    def disable(self): self.enabled = False
'''
    transformed, replacements, metadata = _extract_with_complete_repository(
        source,
        source_class="RecordManager",
        new_class_name="RecordStore",
        methods=["add_record", "remove_record", "record_count"],
        fields=["records"],
    )

    assert replacements == 1
    assert metadata["compatibility"]["property_fields"] == []
    assert "len(self._record_store.records)" in transformed
    assert metadata["validation"]["dependency"] == "PASS"


def test_inherited_source_class_returns_review_required_without_change():
    source = '''class Base:
    pass

class Manager(Base):
    def __init__(self):
        self.items = []
        self.enabled = True
    def add(self, value): self.items.append(value)
    def count(self): return len(self.items)
    def enabled_state(self): return self.enabled
    def disable(self): self.enabled = False
'''
    transformed, replacements, metadata = apply_extract_class(
        source,
        source_class="Manager",
        new_class_name="ItemStore",
        methods_to_extract=["add", "count"],
        fields_to_extract=["items"],
    )

    assert transformed == source
    assert replacements == 0
    assert metadata["status"] == "review_required"
    assert metadata["reason"] == "UNSUPPORTED_INHERITANCE_CASE"


def test_decorated_candidate_returns_review_required_and_preserves_decorator():
    source = '''def audited(function):
    return function

class Manager:
    def __init__(self):
        self.items = []
        self.enabled = True
    @audited
    def add(self, value): self.items.append(value)
    def count(self): return len(self.items)
    def enabled_state(self): return self.enabled
    def disable(self): self.enabled = False
'''
    transformed, replacements, metadata = apply_extract_class(
        source,
        source_class="Manager",
        new_class_name="ItemStore",
        methods_to_extract=["add", "count"],
        fields_to_extract=["items"],
    )

    assert transformed == source
    assert replacements == 0
    assert metadata["reason"] == "UNSUPPORTED_DECORATOR"
    assert "@audited" in transformed


def test_invalid_module_name_is_never_guessed_as_source_class():
    transformed, replacements, metadata = apply_extract_class(
        LIBRARY_SOURCE,
        source_class="manager",
        new_class_name="LoanManager",
    )

    assert transformed == LIBRARY_SOURCE
    assert replacements == 0
    assert metadata["reason"] == "SOURCE_CLASS_NOT_FOUND"


def test_source_file_mismatch_returns_review_required():
    transformed, replacements, metadata = apply_extract_class(
        LIBRARY_SOURCE,
        source_file="other.py",
        current_file_name="manager.py",
        source_class="LibraryManager",
        new_class_name="LoanManager",
    )

    assert transformed == LIBRARY_SOURCE
    assert replacements == 0
    assert metadata["reason"] == "SOURCE_FILE_MISMATCH"


def test_extract_class_is_idempotent():
    transformed, replacements, metadata = apply_extract_class(
        LIBRARY_SOURCE,
        source_class="LibraryManager",
        new_class_name="LoanManager",
        methods_to_extract=["issue_book", "return_book", "count_loans"],
        fields_to_extract=["books", "members", "loans"],
    )
    second, second_replacements, second_metadata = apply_extract_class(
        transformed,
        source_class="LibraryManager",
        new_class_name="LoanManager",
        methods_to_extract=["issue_book", "return_book", "count_loans"],
        fields_to_extract=["books", "members", "loans"],
    )

    assert replacements == 1
    assert metadata["status"] == "success"
    assert second == transformed
    assert second_replacements == 0
    assert second_metadata["status"] == "already_applied"
    assert second.count("class LoanManager:") == 1


def test_large_class_weighted_metrics_and_smell_are_reduced():
    methods = "\n".join(
        f"    def utility_{index}(self): return self.books.get({index})"
        for index in range(1, 18)
    )
    source = f'''class LargeLibrary:
    def __init__(self):
        self.books = {{}}
        self.notices = []
        self.enabled = True
    def add_notice(self, text): self.notices.append(text)
    def latest_notice(self): return self.notices[-1] if self.notices else None
    def enabled_state(self): return self.enabled
    def disable(self): self.enabled = False
{methods}
'''
    transformed, replacements, metadata = _extract_with_complete_repository(
        source,
        source_class="LargeLibrary",
        new_class_name="NoticeBoard",
        methods=["add_notice", "latest_notice"],
        fields=["notices"],
        required_public_fields=["notices"],
    )

    assert replacements == 1
    assert metadata["large_class_before"]["detected"] is True
    assert metadata["large_class_after"]["detected"] is False
    assert metadata["metric_deltas"]["effective_method_count"] > 0
    assert metadata["metric_deltas"]["weighted_complexity"] > 0
    assert metadata["metric_deltas"]["owned_field_count"] > 0
    assert metadata["metric_deltas"]["responsibility_count"] > 0
    assert metadata["validation"]["large_class_reduction"] == "PASS"
    assert "class NoticeBoard:" in transformed


def test_extract_class_preserves_imports_docstrings_comments_and_type_hints():
    source = '''from typing import List

class TypedManager:
    """Coordinates records and enabled state."""
    def __init__(self):
        self.records: List[str] = []
        self.enabled = True

    def add_record(self, value: str) -> None:
        """Add one record."""
        # Keep the audit-facing comment.
        self.records.append(value)

    def record_count(self) -> int:
        return len(self.records)

    def enabled_state(self) -> bool: return self.enabled
    def disable(self) -> None: self.enabled = False
'''
    caller = '''from manager import TypedManager
manager = TypedManager()
manager.add_record("x")
'''
    transformed, replacements, metadata = _extract_with_complete_repository(
        source,
        source_class="TypedManager",
        new_class_name="RecordStore",
        methods=["add_record", "record_count"],
        fields=["records"],
        extra_files=[{"file_name": "caller.py", "source_code": caller, "language": "python"}],
    )

    assert replacements == 1
    assert metadata["status"] == "success"
    assert "from typing import List" in transformed
    assert '"""Coordinates records and enabled state."""' in transformed
    assert '"""Add one record."""' in transformed
    assert "# Keep the audit-facing comment." in transformed
    assert "def add_record(self, value: str) -> None:" in transformed
    assert "def record_count(self) -> int:" in transformed


def test_remaining_large_class_returns_review_required_instead_of_pass():
    methods = "\n".join(
        f"    def utility_{index}(self): return self.books.get({index})"
        for index in range(1, 26)
    )
    source = f'''class VeryLargeLibrary:
    def __init__(self):
        self.books = {{}}
        self.notices = []
        self.enabled = True
    def add_notice(self, text): self.notices.append(text)
    def latest_notice(self): return self.notices[-1] if self.notices else None
    def enabled_state(self): return self.enabled
    def disable(self): self.enabled = False
{methods}
'''
    transformed, replacements, metadata = _extract_with_complete_repository(
        source,
        source_class="VeryLargeLibrary",
        new_class_name="NoticeBoard",
        methods=["add_notice", "latest_notice"],
        fields=["notices"],
    )

    assert transformed == source
    assert replacements == 0
    assert metadata["status"] == "review_required"
    assert metadata["reason"] == "INSUFFICIENT_CLASS_REDUCTION"
    assert metadata["large_class_after"]["detected"] is True
    assert metadata["validation"]["large_class_reduction"] == "FAIL"


def test_library_manager_public_state_and_raw_size_are_preserved_and_reduced():
    utilities = "\n".join(
        f"    def utility_{index}(self): return self.books.get({index})"
        for index in range(1, 18)
    )
    source = f'''"""Library module documentation."""

class LibraryManager:
    def __init__(self):
        self.books = {{}}
        self.notices = []
        self.enabled = True
    def add_notice(self, text): self.notices.append(text)
    def latest_notice(self): return self.notices[-1] if self.notices else None
    def enabled_state(self): return self.enabled
    def disable(self): self.enabled = False
{utilities}

def command_line_flow():
    manager = LibraryManager()
    manager.add_notice("closing soon")
    return manager.latest_notice()
'''
    transformed, replacements, metadata = _extract_with_complete_repository(
        source,
        source_class="LibraryManager",
        new_class_name="LibraryManagerHelper",
        methods=["add_notice", "latest_notice"],
        fields=["notices"],
        required_public_fields=["notices"],
    )

    original_namespace: dict[str, object] = {}
    transformed_namespace: dict[str, object] = {}
    exec(source, original_namespace)
    exec(transformed, transformed_namespace)
    original_manager = original_namespace["LibraryManager"]()
    transformed_manager = transformed_namespace["LibraryManager"]()
    transformed_manager.notices.append("state-compatible")
    original_class = original_namespace["LibraryManager"]
    transformed_class = transformed_namespace["LibraryManager"]

    assert replacements == 1
    assert transformed_manager.notices == ["state-compatible"]
    assert transformed_manager.latest_notice() == "state-compatible"
    assert inspect.signature(transformed_manager.add_notice) == inspect.signature(original_manager.add_notice)
    assert inspect.signature(transformed_class.add_notice) == inspect.signature(original_class.add_notice)
    assert {"add_notice", "latest_notice", "notices"} <= set(dir(transformed_manager))
    assert {"add_notice", "latest_notice", "notices"} <= set(dir(transformed_class))
    assert {"add_notice", "latest_notice", "notices"} <= set(vars(transformed_class))
    assert all(hasattr(transformed_manager, name) for name in ("add_notice", "latest_notice", "notices"))

    replacement_notices = ["replacement-state"]
    transformed_manager.notices = replacement_notices
    assert transformed_manager.notices is replacement_notices
    assert transformed_manager._library_manager_helper.notices is replacement_notices
    assert "notices" not in vars(transformed_manager)
    assert transformed_manager.latest_notice() == "replacement-state"

    assert metadata["validation"]["full_api_preservation"] == "PASS"
    assert metadata["validation"]["state_compatibility"] == "PASS"
    assert metadata["validation"]["single_state_owner"] == "PASS"
    assert metadata["after_metrics"]["method_count"] < metadata["before_metrics"]["method_count"]
    assert metadata["after_metrics"]["loc"] < metadata["before_metrics"]["loc"]
    assert metadata["large_class_after"]["detected"] is False
    assert ast.get_docstring(ast.parse(transformed)) == "Library module documentation."
