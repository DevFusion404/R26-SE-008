"""
unit/test_java_parser.py
-------------------------
Unit tests for java_ast_parser.py.
"""

import pytest
# pyrefly: ignore [missing-import]
import java_ast_parser
# pyrefly: ignore [missing-import]
from java_ast_parser import parse_java_source, parse_java_file, JAVALANG_AVAILABLE


@pytest.mark.unit
class TestJavaASTParser:
    def test_parse_valid_java_features(self):
        if not JAVALANG_AVAILABLE:
            pytest.skip("javalang not installed")

        source = '''\
package com.example;

import java.util.List;
import java.util.Map;

@Deprecated
public abstract class MainService<T> extends BaseService implements IRunner {
    private String name = "සිංහල";
    public static final int MAX = 100;

    public MainService(String name) {
        this.name = name;
    }

    public static void main(String[] args) throws Exception {
        System.out.println("Hello");
        for (int i = 0; i < 10; i++) {
            if (i == 5) break;
        }
        switch (args.length) {
            case 0: break;
            default: break;
        }
    }

    public abstract void run();

    interface InnerInterface {
        void doWork();
    }

    static class NestedClass {
        int x;
    }
}
'''
        res = parse_java_source(source, "MainService.java")
        assert res["file"] == "MainService.java"
        assert res["language"] == "java"
        assert "error" not in res
        ast = res["ast"]
        assert ast["type"] == "CompilationUnit"
        child_types = [c["type"] for c in ast.get("children", [])]
        assert "ImportDeclaration" in child_types
        assert "ClassDeclaration" in child_types

    def test_malformed_java_returns_structured_error(self):
        if not JAVALANG_AVAILABLE:
            pytest.skip("javalang not installed")

        source = "public class Broken {"
        res = parse_java_source(source, "Broken.java")
        assert res["file"] == "Broken.java"
        assert res["language"] == "java"
        assert "error" in res
        assert res["ast"] == {}

    def test_javalang_unavailable_simulation(self, monkeypatch):
        monkeypatch.setattr(java_ast_parser, "JAVALANG_AVAILABLE", False)
        res = parse_java_source("public class A {}", "A.java")
        assert res["file"] == "A.java"
        assert res["language"] == "java"
        assert "error" in res
        assert "javalang library not installed" in res["error"]
        assert res["ast"] == {}

    def test_parse_java_file(self, tmp_path):
        if not JAVALANG_AVAILABLE:
            pytest.skip("javalang not installed")

        f = tmp_path / "App.java"
        f.write_text("public class App {}\n")
        res = parse_java_file(str(f))
        assert res["file"] == "App.java"
        assert res["language"] == "java"
        assert "ast" in res
