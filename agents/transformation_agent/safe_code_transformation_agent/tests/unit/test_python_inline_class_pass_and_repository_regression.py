"""Regression tests for pass-only and repository-aware Python Inline Class."""

import ast

from sctva.transformers import python_inline_class, python_transformers


MAIN_SOURCE = '''from View.view import View
from Controller.controller import Controller
from Model.model import Model


class Main:
    def __init__(self):
        pass

    def user_or_admin(self):
        user_login = input("Login As (admin/user): ")
        if user_login == "admin":
            control_obj.admin_validate_control()
        elif user_login == "user":
            control_obj.user_control()
        else:
            print('Enter "admin" for Admin and "user" for Normal User')


main_obj = Main()
view_obj = View()
control_obj = Controller()
model_obj = Model()
main_obj.user_or_admin()
'''

DATABASE_SOURCE = '''class Database:
    def __init__(self):
        pass

    def connection(self):
        return "connection"
'''

MODEL_SOURCE = '''from Model.database import Database


class Model:
    def __init__(self):
        db = Database()
        self.conn = db.connection()
'''

TABLE_SOURCE = '''from Model.database import Database


class Table(Database):
    def __init__(self):
        db = Database()
        self.conn = db.connection()
'''


def _repo_files():
    return [
        {
            "file_name": "UMS/Model/database.py",
            "language": "python",
            "source_code": DATABASE_SOURCE,
        },
        {
            "file_name": "UMS/Model/model.py",
            "language": "python",
            "source_code": MODEL_SOURCE,
        },
        {
            "file_name": "UMS/Model/table.py",
            "language": "python",
            "source_code": TABLE_SOURCE,
        },
    ]


def test_pass_only_constructor_is_stateless_for_generic_inline():
    tree = ast.parse(MAIN_SOURCE)
    main_class = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "Main"
    )
    constructor = next(
        node for node in main_class.body
        if isinstance(node, ast.FunctionDef) and node.name == "__init__"
    )

    fields, error = python_transformers._inline_class_constructor_fields(constructor)

    assert fields == {}
    assert error == ""


def test_pass_only_constructor_does_not_fail_owned_constructor_analysis():
    tree = ast.parse(DATABASE_SOURCE)
    database_class = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "Database"
    )
    constructor = next(
        node for node in database_class.body
        if isinstance(node, ast.FunctionDef) and node.name == "__init__"
    )

    parameters, defaults, fields, error = python_inline_class._constructor_model(constructor)

    assert parameters == []
    assert defaults == {}
    assert fields == {}
    assert error == "NO_CONSTRUCTOR_FIELDS"


def test_main_pass_only_lazy_class_can_be_inlined_locally():
    transformed, replacements, metadata = python_transformers.apply_inline_class(
        MAIN_SOURCE,
        class_to_inline="Main",
        project_source_files=[
            {
                "file_name": "UMS/index.py",
                "language": "python",
                "source_code": MAIN_SOURCE,
            }
        ],
        current_file_name="UMS/index.py",
    )

    assert metadata["status"] == "success"
    assert replacements > 0
    tree = ast.parse(transformed)
    assert not any(
        isinstance(node, ast.ClassDef) and node.name == "Main"
        for node in tree.body
    )
    assert any(
        isinstance(node, ast.FunctionDef) and node.name == "user_or_admin"
        for node in tree.body
    )
    assert "Main()" not in transformed
    assert "user_or_admin()" in transformed


def test_database_external_dependencies_require_repository_atomic_inline():
    strategy = python_inline_class.select_python_inline_class_strategy(
        DATABASE_SOURCE,
        class_to_inline="Database",
        project_source_files=_repo_files(),
        current_file_name="UMS/Model/database.py",
    )

    assert strategy["status"] == "review_required"
    assert strategy["reason"] == "EXTERNAL_CLASS_REFERENCES_REQUIRE_REPOSITORY_INLINE"
    assert strategy["strategy"] == "repository_atomic_inline_required"
    assert set(strategy["reference_files"]) == {
        "UMS/Model/model.py",
        "UMS/Model/table.py",
    }


def test_database_transformer_preserves_source_when_repository_dependencies_exist():
    transformed, replacements, metadata = python_transformers.apply_inline_class(
        DATABASE_SOURCE,
        class_to_inline="Database",
        project_source_files=_repo_files(),
        current_file_name="UMS/Model/database.py",
    )

    assert transformed == DATABASE_SOURCE
    assert replacements == 0
    assert metadata["status"] == "review_required"
    assert metadata["reason"] == "EXTERNAL_CLASS_REFERENCES_REQUIRE_REPOSITORY_INLINE"
    assert set(metadata["reference_files"]) == {
        "UMS/Model/model.py",
        "UMS/Model/table.py",
    }


def test_complex_constructor_remains_safely_rejected():
    source = '''class Helper:
    def __init__(self):
        self.value = build_value()

    def show(self):
        return self.value


helper = Helper()
print(helper.show())
'''

    transformed, replacements, metadata = python_transformers.apply_inline_class(
        source,
        class_to_inline="Helper",
    )

    assert transformed == source
    assert replacements == 0
    assert metadata["status"] == "review_required"
    assert metadata["reason"] in {
        "CONSTRUCTOR_FIELD_EXPRESSION_UNSAFE",
        "CONSTRUCTOR_FIELD_VALUE_UNSUPPORTED",
    }


def test_database_repository_atomic_inline_rewrites_all_participants():
    transaction = python_inline_class.apply_repository_inline_class_transaction(
        _repo_files(),
        target_file_name="UMS/Model/database.py",
        class_to_inline="Database",
    )

    assert transaction["status"] == "success"
    assert transaction["strategy"] == "repository_atomic_module_function"
    assert set(transaction["transformed_sources"]) == {
        "UMS/Model/database.py",
        "UMS/Model/model.py",
        "UMS/Model/table.py",
    }

    database = transaction["transformed_sources"]["UMS/Model/database.py"]
    model = transaction["transformed_sources"]["UMS/Model/model.py"]
    table = transaction["transformed_sources"]["UMS/Model/table.py"]

    assert "class Database" not in database
    assert "def connection(" in database
    assert "from Model.database import connection" in model
    assert "Database()" not in model
    assert "self.conn = connection()" in model
    assert "from Model.database import connection" in table
    assert "class Table(Database)" not in table
    assert "class Table:" in table
    assert "Database()" not in table
    assert "self.conn = connection()" in table

    for source in (database, model, table):
        ast.parse(source)
        compile(source, "<repository-inline-test>", "exec")


def test_repository_inline_rejects_subclass_that_uses_inherited_method():
    unsafe_table = '''from Model.database import Database


class Table(Database):
    def ping(self):
        return self.connection()
'''
    repository = [
        {
            "file_name": "UMS/Model/database.py",
            "language": "python",
            "source_code": DATABASE_SOURCE,
        },
        {
            "file_name": "UMS/Model/table.py",
            "language": "python",
            "source_code": unsafe_table,
        },
    ]

    transaction = python_inline_class.plan_repository_inline_class_transaction(
        repository,
        target_file_name="UMS/Model/database.py",
        class_to_inline="Database",
    )

    assert transaction["status"] == "review_required"
    assert transaction["reason"] == "SUBCLASS_USES_INHERITED_MEMBER_REQUIRES_REWRITE"


def test_repository_inline_rejects_stateful_cross_file_class():
    stateful = '''class Database:
    def __init__(self):
        self.mode = "rw"

    def connection(self):
        return self.mode
'''
    consumer = '''from Model.database import Database


def use_database():
    db = Database()
    return db.connection()
'''
    repository = [
        {
            "file_name": "UMS/Model/database.py",
            "language": "python",
            "source_code": stateful,
        },
        {
            "file_name": "UMS/consumer.py",
            "language": "python",
            "source_code": consumer,
        },
    ]

    transaction = python_inline_class.plan_repository_inline_class_transaction(
        repository,
        target_file_name="UMS/Model/database.py",
        class_to_inline="Database",
    )

    assert transaction["status"] == "review_required"
    assert transaction["reason"] == "REPOSITORY_INLINE_STATEFUL_CLASS_UNSUPPORTED"
