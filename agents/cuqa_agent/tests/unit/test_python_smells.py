"""
unit/test_python_smells.py
---------------------------
Comprehensive unit tests for every Python code smell detector in report_generator.py.
"""

import pytest
# pyrefly: ignore [missing-import]
from report_generator import generate_file_report


@pytest.mark.unit
class TestPythonSmells:

    # ── 1. LongMethod ────────────────────────────────────────────────────────
    def test_long_method_boundary_30_lines_no_smell(self):
        # 30 body lines (end_lineno - lineno = 30) -> threshold is > 30, so 30 does NOT trigger
        body = "\n".join([f"    x{i} = {i}" for i in range(29)])
        source = f"def fn():\n{body}\n    return 0\n"
        report = generate_file_report(source, "test.py")
        smells = [s for s in report["code_smells"] if s["type"] == "LongMethod"]
        assert len(smells) == 0

    def test_long_method_31_lines_triggers(self):
        # 31 body lines -> triggers LongMethod
        body = "\n".join([f"    x{i} = {i}" for i in range(30)])
        source = f"def fn():\n{body}\n    return 0\n"
        report = generate_file_report(source, "test.py")
        smells = [s for s in report["code_smells"] if s["type"] == "LongMethod"]
        assert len(smells) == 1
        s = smells[0]
        assert s["severity"] == "high"
        assert s["entity"] == "fn"
        assert s["line"] == 1
        assert "parameter_count" in s
        assert "start_line" in s
        assert "end_line" in s
        assert "cyclomatic_complexity" in s

    # ── 2. TooManyParameters ─────────────────────────────────────────────────
    def test_too_many_parameters_boundary(self):
        # 5 real parameters -> should NOT trigger (> 5 required)
        source_5 = "def fn(a, b, c, d, e):\n    return a\n"
        report_5 = generate_file_report(source_5, "test.py")
        smells_5 = [s for s in report_5["code_smells"] if s["type"] == "TooManyParameters"]
        assert len(smells_5) == 0

        # 6 real parameters -> TRIGGERS
        source_6 = "def fn(a, b, c, d, e, f):\n    return a\n"
        report_6 = generate_file_report(source_6, "test.py")
        smells_6 = [s for s in report_6["code_smells"] if s["type"] == "TooManyParameters"]
        assert len(smells_6) == 1
        assert smells_6[0]["severity"] == "medium"
        assert smells_6[0]["parameter_count"] == 6

    def test_too_many_parameters_ignores_self_and_cls(self):
        # self + 5 params = 5 real -> does NOT trigger
        source_self_5 = "class C:\n    def m(self, a, b, c, d, e):\n        return a\n"
        rep = generate_file_report(source_self_5, "test.py")
        smells = [s for s in rep["code_smells"] if s["type"] == "TooManyParameters"]
        assert len(smells) == 0

        # self + 6 params = 6 real -> TRIGGERS
        source_self_6 = "class C:\n    def m(self, a, b, c, d, e, f):\n        return a\n"
        rep6 = generate_file_report(source_self_6, "test.py")
        smells6 = [s for s in rep6["code_smells"] if s["type"] == "TooManyParameters"]
        assert len(smells6) == 1

    # ── 3. SwitchStatements ──────────────────────────────────────────────────
    def test_switch_statements_boundary(self):
        # 3 elif branches -> does NOT trigger (requires >= 4)
        src_3 = "def fn(x):\n    if x==1:\n        pass\n    elif x==2:\n        pass\n    elif x==3:\n        pass\n    elif x==4:\n        pass\n"
        r3 = generate_file_report(src_3, "test.py")
        assert not any(s["type"] == "SwitchStatements" for s in r3["code_smells"])

        # 4 elif branches -> TRIGGERS
        src_4 = "def fn(x):\n    if x==1:\n        pass\n    elif x==2:\n        pass\n    elif x==3:\n        pass\n    elif x==4:\n        pass\n    elif x==5:\n        pass\n"
        r4 = generate_file_report(src_4, "test.py")
        s4 = [s for s in r4["code_smells"] if s["type"] == "SwitchStatements"]
        assert len(s4) == 1
        assert s4[0]["severity"] == "medium"

    # ── 4. MessageChains ─────────────────────────────────────────────────────
    def test_message_chains(self):
        # chain depth 2 -> no smell
        src_2 = "def fn(obj):\n    return obj.a.b\n"
        r2 = generate_file_report(src_2, "test.py")
        assert not any(s["type"] == "MessageChains" for s in r2["code_smells"])

        # chain depth 3 -> TRIGGERS
        src_3 = "def fn(obj):\n    return obj.a.b.c\n"
        r3 = generate_file_report(src_3, "test.py")
        s3 = [s for s in r3["code_smells"] if s["type"] == "MessageChains"]
        assert len(s3) == 1
        assert s3[0]["chain_length"] >= 3

    # ── 5. LargeClass ────────────────────────────────────────────────────────
    def test_large_class_boundary(self):
        # 15 methods -> no smell (> 15 required)
        methods_15 = "\n".join([f"    def m{i}(self): pass" for i in range(15)])
        src_15 = f"class Foo:\n{methods_15}\n"
        r15 = generate_file_report(src_15, "test.py")
        assert not any(s["type"] == "LargeClass" for s in r15["code_smells"])

        # 16 methods -> TRIGGERS
        methods_16 = "\n".join([f"    def m{i}(self): pass" for i in range(16)])
        src_16 = f"class Foo:\n{methods_16}\n"
        r16 = generate_file_report(src_16, "test.py")
        s16 = [s for s in r16["code_smells"] if s["type"] == "LargeClass"]
        assert len(s16) == 1
        assert s16[0]["method_count"] == 16
        assert s16[0]["severity"] == "high"

    # ── 6. LazyClass ─────────────────────────────────────────────────────────
    def test_lazy_class(self):
        src = "class Tiny:\n    def one(self): pass\n"
        r = generate_file_report(src, "test.py")
        s = [s for s in r["code_smells"] if s["type"] == "LazyClass"]
        assert len(s) == 1
        assert s[0]["severity"] == "low"

    # ── 7. PrimitiveObsession ────────────────────────────────────────────────
    def test_primitive_obsession(self):
        src = "class User:\n    name: str\n    age: int\n    height: float\n    active: bool\n"
        r = generate_file_report(src, "test.py")
        s = [s for s in r["code_smells"] if s["type"] == "PrimitiveObsession"]
        assert len(s) == 1
        assert s[0]["primitive_field_count"] == 4

    # ── 8. InappropriateIntimacy ─────────────────────────────────────────────
    def test_inappropriate_intimacy(self):
        src = "class C:\n    def m(self, obj):\n        return obj._secret\n"
        r = generate_file_report(src, "test.py")
        s = [s for s in r["code_smells"] if s["type"] == "InappropriateIntimacy"]
        assert len(s) == 1

    # ── 9. SpeculativeGenerality ─────────────────────────────────────────────
    def test_speculative_generality(self):
        src = "from abc import ABC\nclass AbstractFoo(ABC):\n    pass\n"
        r = generate_file_report(src, "test.py")
        s = [s for s in r["code_smells"] if s["type"] == "SpeculativeGenerality"]
        assert len(s) == 1

    # ── 10. MagicNumber ──────────────────────────────────────────────────────
    def test_magic_number(self):
        src_safe = "x = 0\ny = 1\nz = -1\nw = 2\nb = True\n"
        r_safe = generate_file_report(src_safe, "test.py")
        assert not any(s["type"] == "MagicNumber" for s in r_safe["code_smells"])

        src_magic = "x = 999\n"
        r_magic = generate_file_report(src_magic, "test.py")
        s_magic = [s for s in r_magic["code_smells"] if s["type"] == "MagicNumber"]
        assert len(s_magic) == 1
        assert s_magic[0]["severity"] == "low"

    # ── 11. BareExcept ────────────────────────────────────────────────────────
    def test_bare_except(self):
        src = "try:\n    x = 1\nexcept:\n    pass\n"
        r = generate_file_report(src, "test.py")
        s = [s for s in r["code_smells"] if s["type"] == "BareExcept"]
        assert len(s) == 1
        assert s[0]["severity"] == "medium"

    # ── 12. DeadCode ─────────────────────────────────────────────────────────
    def test_dead_code(self):
        src = "def unused_fn():\n    return 42\n"
        r = generate_file_report(src, "test.py")
        s = [s for s in r["code_smells"] if s["type"] == "DeadCode"]
        assert len(s) == 1
        assert s[0]["entity"] == "unused_fn"

    # ── 13. DuplicateCode ────────────────────────────────────────────────────
    def test_duplicate_code(self):
        src = '''\
def f1(a, b):
    v = a + b
    v = v * 10
    v = v - 5
    return v

def f2(x, y):
    v = x + y
    v = v * 10
    v = v - 5
    return v
'''
        r = generate_file_report(src, "test.py")
        s = [s for s in r["code_smells"] if s["type"] == "DuplicateCode"]
        assert len(s) == 1

    # ── 14. FeatureEnvy ──────────────────────────────────────────────────────
    def test_feature_envy(self):
        src = '''\
class C:
    def m(self, o):
        a = o.x
        b = o.y
        c = o.z
        return a + b + c
'''
        r = generate_file_report(src, "test.py")
        s = [s for s in r["code_smells"] if s["type"] == "FeatureEnvy"]
        assert len(s) == 1

    # ── 15. DataClumps ───────────────────────────────────────────────────────
    def test_data_clumps(self):
        src = '''\
def f1(p1, p2, p3):
    return p1 + p2 + p3

def f2(p1, p2, p3, p4):
    return p1 + p2 + p3 + p4
'''
        r = generate_file_report(src, "test.py")
        s = [s for s in r["code_smells"] if s["type"] == "DataClumps"]
        assert len(s) == 1

    # ── 16. Comments ─────────────────────────────────────────────────────────
    def test_comments_smell(self):
        # > 50 LOC and comment ratio > 0.3
        comments = "\n".join([f"# comment line {i}" for i in range(25)])
        code = "\n".join([f"x_{i} = {i}" for i in range(35)])
        src = f"{comments}\n{code}\n"
        r = generate_file_report(src, "test.py")
        s = [s for s in r["code_smells"] if s["type"] == "Comments"]
        assert len(s) == 1
        assert s[0]["severity"] == "low"
