"""
regression/test_smell_false_positives.py
----------------------------------------
Regression tests for detections that were reported on the wrong line or that
were not real code smells (found on curl's docs/examples/ghiper.c and on
typical Python / Java code).
"""

import pytest
# pyrefly: ignore [missing-import]
from report_generator import generate_file_report
# pyrefly: ignore [missing-import]
from java_ast_parser import JAVALANG_AVAILABLE


def _smells(source: str, filename: str, smell_type: str) -> list[dict]:
    report = generate_file_report(source, filename, detector="off")
    return [s for s in report["code_smells"] if s["type"] == smell_type]


def _line_of(source: str, needle: str) -> int:
    for no, line in enumerate(source.splitlines(), start=1):
        if needle in line:
            return no
    raise AssertionError(f"{needle!r} not in source")


LICENSE_HEADER = "/*" + "\n *  license text line" * 20 + "\n */\n"


# ---------------------------------------------------------------------------
# C
# ---------------------------------------------------------------------------

@pytest.mark.regression
class TestCLineNumbers:
    def test_findings_keep_their_line_after_a_block_comment(self):
        src = LICENSE_HEADER + (
            "#include <string.h>\n"
            "\n"
            "/* Copy a string\n"
            "   into the buffer */\n"
            "static void copy(char *out, const char *in, int n)\n"
            "{\n"
            "  if(n > 37) {\n"
            "    strcpy(out, in);\n"
            "  }\n"
            "}\n"
            "int main(void) { char b[8]; copy(b, \"x\", 1); return 0; }\n"
        )
        magic = _smells(src, "ghiper.c", "MagicNumber")
        assert [s["line"] for s in magic] == [_line_of(src, "n > 37")]
        assert magic[0]["entity"] == "copy"
        unsafe = _smells(src, "ghiper.c", "UnsafeFunctionUsage")
        assert [s["line"] for s in unsafe] == [_line_of(src, "strcpy(out")]

    def test_url_in_string_does_not_hide_the_rest_of_the_line(self):
        src = 'void f(int x) {\n  puts("http://example.com"); if (x == 37) x = 0;\n}\n'
        magic = _smells(src, "net.c", "MagicNumber")
        assert len(magic) == 1 and magic[0]["line"] == 2

    def test_brace_in_char_literal_does_not_end_the_function(self):
        src = (
            "int count(const char *s) {\n"
            "  int n = 0;\n"
            "  while (*s) { if (*s == '}') n++; s++; }\n"
            "  return n * 37;\n"
            "}\n"
        )
        magic = _smells(src, "count.c", "MagicNumber")
        assert [s["line"] for s in magic] == [4]


@pytest.mark.regression
class TestCMagicNumberContext:
    def test_unit_conversion_and_permission_modes_not_reported(self):
        src = (
            "void t(long timeout_ms, struct timeval *tv) {\n"
            "  tv->tv_sec = timeout_ms / 1000;\n"
            "  tv->tv_usec = (timeout_ms % 1000) * 1000;\n"
            "  mkfifo(\"hiper.fifo\", 0600);\n"
            "}\n"
        )
        assert _smells(src, "timer.c", "MagicNumber") == []

    def test_declarations_and_preprocessor_not_reported(self):
        src = (
            "enum color { RED = 10, GREEN = 20 };\n"
            "static const int LIMITS[] = { 3, 5, 7 };\n"
            "static int table[] = { 11, 13, 17 };\n"
            "struct flags { unsigned mode : 3; };\n"
            "void f(int code) {\n"
            "#define BUF_SIZE 300\n"
            "  char line[300];\n"
            "  const int retries = 7;\n"
            "  int primes[] = { 19, 23 };\n"
            "  switch (code) {\n"
            "  case 42: break;\n"
            "  }\n"
            "}\n"
        )
        assert _smells(src, "decls.c", "MagicNumber") == []

    def test_number_after_case_label_is_still_reported(self):
        src = "int f(int c) {\n  switch (c) {\n  case 3: return c * 37;\n  }\n  return 0;\n}\n"
        magic = _smells(src, "sw.c", "MagicNumber")
        assert [s["message"] for s in magic] == ["Magic number 37 detected"]

    def test_subtraction_is_not_a_negative_literal(self):
        src = "int f(int x) {\n  int a = x - 2;\n  int b = -5;\n  return a + b;\n}\n"
        magic = _smells(src, "neg.c", "MagicNumber")
        assert [s["message"] for s in magic] == ["Magic number -5 detected"]

    def test_real_magic_numbers_still_reported(self):
        src = "void f(CURLM *m) {\n  curl_multi_setopt(m, CURLMOPT_MAXCONNECTS, 30L);\n  sleep(5);\n}\n"
        magic = _smells(src, "multi.c", "MagicNumber")
        assert [s["line"] for s in magic] == [2, 3]


@pytest.mark.regression
class TestCFunctionSmells:
    def test_deep_nesting_counts_control_flow_only(self):
        src = (
            "void init(void) {\n"
            "  struct pt p = { { 1, 2 }, { 3, 4 } };\n"
            "  if (a) { if (b) { if (c) { use(p); } } }\n"
            "}\n"
        )
        assert _smells(src, "init.c", "DeepNesting") == []

    def test_deep_nesting_reported_on_the_right_function(self):
        src = LICENSE_HEADER + (
            "static int fifo_cb(int ch) {\n"
            "  do {\n"
            "    if (ch) {\n"
            "      while (ch) {\n"
            "        if (ch > 1) { ch--; }\n"
            "      }\n"
            "    }\n"
            "  } while (ch);\n"
            "  return ch;\n"
            "}\n"
            "\n"
            "int init_fifo(void) {\n"
            "  if (x) { if (y) { return 1; } }\n"
            "  return fifo_cb(0);\n"
            "}\n"
        )
        nesting = _smells(src, "ghiper.c", "DeepNesting")
        assert [s["entity"] for s in nesting] == ["fifo_cb"]
        assert nesting[0]["nesting_depth"] == 4
        assert nesting[0]["line"] == _line_of(src, "static int fifo_cb")

    def test_pointer_returning_function_is_found(self):
        src = "static char *dup_url(const char *src)\n{\n  return strdup(src);\n}\n"
        dead = _smells(src, "url.c", "DeadCode")
        assert [s["entity"] for s in dead] == ["dup_url"]

    def test_prototype_is_not_a_use_but_function_pointer_is(self):
        src = (
            "static void unused(void);\n"
            "static void callback(void);\n"
            "static void (*handlers[])(void) = { callback };\n"
            "static void unused(void) { }\n"
            "static void callback(void) { }\n"
        )
        dead = _smells(src, "cb.c", "DeadCode")
        assert [s["entity"] for s in dead] == ["unused"]

    def test_static_inline_in_header_is_not_dead(self):
        src = "static inline int add(int a, int b) { return a + b; }\n"
        assert _smells(src, "util.h", "DeadCode") == []

    def test_banned_function_macro_is_not_a_call(self):
        src = "#undef strcpy\n#define strcpy(a, b) curl_strcpy_is_banned\nvoid f(void) { }\n"
        assert _smells(src, "setup.c", "UnsafeFunctionUsage") == []

    def test_constants_and_extern_are_not_global_variables(self):
        src = "static const int MAX = 5;\nextern int shared;\nint counter;\nchar *name;\n"
        names = [s["entity"] for s in _smells(src, "g.c", "GlobalVariable")]
        assert names == ["counter", "name"]

    def test_trivial_getters_are_not_duplicate_code(self):
        src = "int get_a(void) { return a; }\nint get_b(void) { return b; }\n"
        assert _smells(src, "get.c", "DuplicateCode") == []

    def test_real_clones_are_duplicate_code(self):
        body = "{\n  int v = a + b;\n  v = v * 10;\n  v = v - 5;\n  return v;\n}\n"
        src = "int f1(int a, int b) " + body + "int f2(int x, int y) " + body.replace("a + b", "x + y")
        assert len(_smells(src, "dup.c", "DuplicateCode")) == 2


# ---------------------------------------------------------------------------
# Java
# ---------------------------------------------------------------------------

@pytest.mark.regression
class TestJavaMagicNumbers:
    @pytest.fixture(autouse=True)
    def _need_javalang(self):
        if not JAVALANG_AVAILABLE:
            pytest.skip("javalang not installed")

    SOURCE = (
        "/*\n * License header\n * spanning lines\n */\n"
        "public class Sample {\n"
        "    private static final int[] PRIMES = {\n"
        "        3, 5, 7\n"
        "    };\n"
        "    enum Color { RED(255, 10, 20); Color(int r, int g, int b) {} }\n"
        "    @Size(max = 64)\n"
        "    private String name;\n"
        "    public int compute(int x, long ms) {\n"
        "        String msg = \"Error 404 at step 77\";\n"
        "        long seconds = ms / 1000;\n"
        "        switch (x) { case 9: return 0; }\n"
        "        int neg = -5;\n"
        "        return x * 37;\n"
        "    }\n"
        "    public int hashCode() { return 31 * name.hashCode(); }\n"
        "}\n"
    )

    def test_each_literal_reported_once_on_its_own_line(self):
        magic = _smells(self.SOURCE, "Sample.java", "MagicNumber")
        found = sorted((s["line"], s["message"]) for s in magic)
        assert found == [
            (_line_of(self.SOURCE, "int neg = -5"), "Magic number -5 detected"),
            (_line_of(self.SOURCE, "x * 37"), "Magic number 37 detected"),
        ]
        assert all(s["entity"] == "compute" for s in magic)

    def test_magic_numbers_in_test_classes_are_expected_values(self):
        src = "public class ParserTest {\n    public void testSize() {\n        assertEquals(37, size());\n    }\n}\n"
        assert _smells(src, "ParserTest.java", "MagicNumber") == []

    def test_constructor_parameters_are_checked(self):
        src = "public class P {\n    public P(int a, int b, int c, int d, int e, int f) {}\n}\n"
        smells = _smells(src, "P.java", "TooManyParameters")
        assert [s["entity"] for s in smells] == ["P"]

    def test_javadoc_does_not_count_as_comment_smell(self):
        methods = "\n".join(
            f"    /**\n     * Returns value {i}.\n     * @return the value\n     */\n"
            f"    public int v{i}() {{ return x; }}"
            for i in range(12)
        )
        src = f"public class Api {{\n    int x;\n{methods}\n}}\n"
        assert _smells(src, "Api.java", "Comments") == []


# ---------------------------------------------------------------------------
# Python
# ---------------------------------------------------------------------------

@pytest.mark.regression
class TestPythonClassSmells:
    def test_small_by_design_classes_are_not_lazy(self):
        src = (
            "from dataclasses import dataclass\n"
            "from enum import Enum\n"
            "from pydantic import BaseModel\n"
            "class ConfigError(Exception):\n    pass\n"
            "class Color(Enum):\n    RED = 1\n"
            "@dataclass\nclass Point:\n    x: int\n"
            "class User(BaseModel):\n    name: str\n"
        )
        assert _smells(src, "models.py", "LazyClass") == []

    def test_class_inheriting_a_base_is_not_speculative(self):
        src = (
            "from abc import ABC, abstractmethod\n"
            "from pydantic import BaseModel\n"
            "class User(BaseModel):\n    name: str\n"
            "class Shape(ABC):\n    @abstractmethod\n    def area(self): ...\n"
            "class Square(Shape):\n    def area(self):\n        return 1\n"
        )
        assert _smells(src, "shapes.py", "SpeculativeGenerality") == []

    def test_same_class_and_protocol_access_is_not_intimacy(self):
        src = (
            "import sys\n"
            "class Money:\n"
            "    def __eq__(self, other):\n        return self._cents == other._cents\n"
            "    def dump(self, obj, point):\n"
            "        frame = sys._getframe()\n"
            "        return obj.__dict__, point._replace(x=1), frame\n"
        )
        assert _smells(src, "money.py", "InappropriateIntimacy") == []

    def test_class_using_its_own_private_members_is_not_intimacy(self):
        src = (
            "class Validator:\n"
            "    @staticmethod\n    def _check(x):\n        return x\n"
            "    def run(self, item):\n        return Validator._check(item)\n"
        )
        assert _smells(src, "v.py", "InappropriateIntimacy") == []

    def test_knowledge_base_is_not_an_abstract_base(self):
        src = "class RefactoringKnowledgeBase:\n    def lookup(self):\n        return 1\n"
        assert _smells(src, "kb.py", "SpeculativeGenerality") == []

    def test_framework_visitor_is_not_lazy_or_envious(self):
        src = (
            "import ast\n"
            "class Renamer(ast.NodeTransformer):\n"
            "    def visit_Name(self, node):\n"
            "        return ast.Name(id=node.id.upper(), ctx=node.ctx, lineno=node.lineno)\n"
        )
        assert _smells(src, "r.py", "LazyClass") == []
        assert _smells(src, "r.py", "FeatureEnvy") == []

    def test_test_classes_are_not_dead_code(self):
        src = "class TestParser:\n    def test_empty(self):\n        assert True\n"
        assert _smells(src, "parser_cases.py", "DeadCode") == []


@pytest.mark.regression
class TestPythonFunctionSmells:
    def test_methods_and_decorated_functions_are_not_dead_code(self):
        src = (
            "import pytest\n"
            "class Repo:\n"
            "    def save(self):\n        self._flush()\n"
            "    def _flush(self):\n        pass\n"
            "@pytest.fixture\ndef repo():\n    return Repo()\n"
        )
        dead = [s["entity"] for s in _smells(src, "repo.py", "DeadCode")]
        assert dead == []

    def test_unused_private_method_is_dead_code(self):
        src = "class Repo:\n    def _old(self):\n        pass\nRepo()\n"
        assert [s["entity"] for s in _smells(src, "repo.py", "DeadCode")] == ["_old"]

    def test_trivial_getters_are_not_duplicate_code(self):
        src = (
            "class A:\n    def get_a(self):\n        return self.client.a\n"
            "    def get_b(self):\n        return self.client.b\n"
        )
        assert _smells(src, "a.py", "DuplicateCode") == []

    def test_module_calls_are_not_feature_envy(self):
        src = (
            "import os, json\n"
            "class Store:\n"
            "    def save(self, path):\n"
            "        data = json.dumps({})\n"
            "        full = os.path.join(os.path.dirname(path), os.path.basename(path))\n"
            "        return full, data\n"
            "def helper(cfg):\n    return cfg.a, cfg.b, cfg.c\n"
        )
        assert _smells(src, "store.py", "FeatureEnvy") == []

    def test_namespace_and_self_chains_are_not_message_chains(self):
        src = (
            "import datetime\n"
            "class C:\n"
            "    def m(self):\n"
            "        stamp = datetime.datetime.now().isoformat()\n"
            "        return self.client.session.get(stamp)\n"
        )
        assert _smells(src, "c.py", "MessageChains") == []

    def test_separate_if_elif_pairs_are_not_a_switch(self):
        pair = "    if x == {0}:\n        pass\n    elif x == {1}:\n        pass\n"
        src = "def f(x):\n" + "".join(pair.format(i, i + 10) for i in range(5))
        assert _smells(src, "f.py", "SwitchStatements") == []

    def test_global_write_is_not_an_unused_variable(self):
        src = "counter = 0\ndef bump():\n    global counter\n    counter = 5\n"
        assert _smells(src, "c.py", "UnusedVariable") == []

    def test_unreachable_code_reported_once(self):
        src = (
            "def outer():\n"
            "    def inner():\n        return 1\n        x = 2\n"
            "    try:\n        pass\n"
            "    except ValueError:\n        raise\n        y = 3\n"
            "    return inner\n"
        )
        lines = [s["line"] for s in _smells(src, "u.py", "UnreachableCode")]
        assert sorted(lines) == [4, 9]

    def test_overrides_are_not_data_clumps(self):
        src = (
            "class A:\n    def forward(self, x, mask, cache):\n        return x\n"
            "class B(A):\n    def forward(self, x, mask, cache):\n        return mask\n"
        )
        assert _smells(src, "m.py", "DataClumps") == []

    def test_named_numbers_are_not_magic(self):
        src = (
            "RETRY_DELAYS = [1, 5, 10]\n"
            "LIMITS = {'cpu': 80}\n"
            "class Config:\n    timeout = 30\n"
            "def connect(host, timeout=45):\n"
            "    elapsed_ms = timeout * 1000\n"
            "    return open_conn(host, retries=3), elapsed_ms\n"
        )
        assert _smells(src, "net.py", "MagicNumber") == []

    def test_string_pipeline_is_not_a_message_chain(self):
        src = "def norm(value):\n    return str(value).replace('\\\\', '/').strip().lower()\n"
        assert _smells(src, "n.py", "MessageChains") == []

    def test_magic_numbers_in_test_files_are_expected_values(self):
        src = "def test_size():\n    assert size() == 4096 * 3\n    data = 'x' * 500\n"
        assert _smells(src, "test_upload.py", "MagicNumber") == []
        assert _smells(src, "upload.py", "MagicNumber") != []

    def test_license_header_is_not_a_comment_smell(self):
        header = "\n".join(f"# Copyright line {i}" for i in range(20))
        code = "\n".join(f"x_{i} = {i}" for i in range(35))
        assert _smells(f"{header}\n{code}\n", "lic.py", "Comments") == []
