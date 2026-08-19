"""
unit/test_c_parser.py
----------------------
Unit tests for c_ast_parser.py (both tree-sitter and regex fallback modes).
"""

import pytest
# pyrefly: ignore [missing-import]
import c_ast_parser
# pyrefly: ignore [missing-import]
from c_ast_parser import parse_c_source, parse_c_file


@pytest.mark.unit
class TestCASTParser:
    def test_parse_valid_c_features(self):
        source = '''\
#include <stdio.h>
#include "my_header.h"

#define MAX_SIZE 100

typedef struct {
    int id;
    char name[50];
} User;

enum Status { IDLE, RUNNING, STOPPED };

union Data {
    int i;
    float f;
};

static int g_counter = 0;
const char *TITLE = "C Program";

int (*handler)(int);

void process(int *ptr, int arr[]) {
    // Single line comment
    /* Multiline comment
       with multiple lines */
    if (ptr != NULL) {
        for (int i = 0; i < 10; i++) {
            while (g_counter < 5) {
                g_counter++;
            }
        }
    }
}

int main(void) {
    char *escaped = "Line1\\nLine2";
    process(NULL, NULL);
    return 0;
}
'''
        res = parse_c_source(source, "main.c")
        assert res["file"] == "main.c"
        assert res["language"] == "c"
        assert "error" not in res
        assert res["ast"]["type"] == "TranslationUnit"
        assert len(res["ast"].get("children", [])) > 0

    def test_empty_c_source(self):
        res = parse_c_source("", "empty.c")
        assert res["file"] == "empty.c"
        assert res["language"] == "c"
        assert res["ast"]["type"] == "TranslationUnit"

    def test_header_file(self):
        source = "#pragma once\nint add(int a, int b);\n"
        res = parse_c_source(source, "utils.h")
        assert res["file"] == "utils.h"
        assert res["language"] == "c"
        assert res["ast"]["type"] == "TranslationUnit"

    def test_tree_sitter_fallback_mode_simulation(self, monkeypatch):
        # Force tree-sitter to be unavailable
        monkeypatch.setattr(c_ast_parser, "_TREE_SITTER_AVAILABLE", False)

        source = "int foo(int x) { return x + 1; }"
        res = parse_c_source(source, "foo.c")
        assert res["language"] == "c"
        assert res["parser"] == "regex-fallback"
        assert res["ast"]["type"] == "TranslationUnit"

    def test_complex_declaration_does_not_crash(self):
        source = "int (*func_ptr_array[10])(int, void *);"
        res = parse_c_source(source, "complex.c")
        assert res["language"] == "c"
        assert "ast" in res

    def test_parse_c_file(self, tmp_path):
        f = tmp_path / "test.c"
        f.write_text("int main() { return 0; }\n")
        res = parse_c_file(str(f))
        assert res["file"] == "test.c"
        assert res["language"] == "c"
        assert "ast" in res
