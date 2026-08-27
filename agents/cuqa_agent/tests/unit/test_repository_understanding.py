"""
unit/test_repository_understanding.py
--------------------------------------
Unit tests for agents/cuqa_agent/src/repository_understanding.py.

Tests directory classification, file classification, entry point detection,
dependency graph extraction, artifact detection, reading path generation,
architectural clue detection, monorepo/subproject detection, and edge cases.
"""

import os
import sys
from pathlib import Path
import pytest

SRC_DIR = Path(__file__).resolve().parent.parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from repository_understanding import (
    classify_directory_role,
    classify_file_role,
    detect_entry_points,
    detect_project_artifacts,
    build_dependency_graph,
    identify_key_files,
    identify_important_directories,
    generate_reading_path,
    detect_architectural_clues,
    detect_subprojects,
    count_lines_of_code,
    count_directories,
    analyze_repository_overview,
)


@pytest.mark.unit
class TestRepositoryUnderstandingUnit:
    # ── Directory Role Classification ─────────────────────────────────────────

    def test_classify_directory_role_known_names(self):
        roles = [
            ("src", "Source", "high"),
            ("services", "Service", "high"),
            ("controllers", "Controller", "high"),
            ("models", "Data Model", "high"),
            ("tests", "Tests", "high"),
            ("config", "Configuration", "high"),
            ("docs", "Documentation", "high"),
            ("include", "Headers", "high"),
            ("repositories", "Repository", "high"),
        ]
        for name, expected_role, expected_conf in roles:
            res = classify_directory_role(name, [])
            assert res["role"] == expected_role
            assert res["confidence"] == expected_conf

    def test_classify_directory_role_partial_match(self):
        res = classify_directory_role("user_services", [])
        assert res["role"] == "Service"
        assert res["confidence"] == "medium"

    def test_classify_directory_role_content_fallback(self):
        res = classify_directory_role("foo", ["test_bar.py"])
        assert res["role"] == "Tests"

        res = classify_directory_role("bar", ["header.h"])
        assert res["role"] == "Headers"

    def test_classify_directory_role_unknown(self):
        res = classify_directory_role("random_dir_123", ["data.bin"])
        assert res["role"] == "Unknown"
        assert res["confidence"] == "low"

    # ── File Role Classification ──────────────────────────────────────────────

    def test_classify_file_role_exact_filenames(self):
        files = [
            ("README.md", "Documentation"),
            ("requirements.txt", "Dependency Manifest"),
            ("pom.xml", "Build Configuration"),
            ("Dockerfile", "Deployment"),
            ("LICENSE", "License"),
        ]
        for name, expected_role in files:
            res = classify_file_role(name)
            assert res["role"] == expected_role
            assert res["confidence"] == "high"

    def test_classify_file_role_python_content(self):
        res = classify_file_role("src/start.py", source="if __name__ == '__main__': pass")
        assert res["role"] == "Entry Point"
        assert res["confidence"] == "high"

    def test_classify_file_role_java_content(self):
        res = classify_file_role("Main.java", source="public static void main(String[] args) {}")
        assert res["role"] == "Entry Point"
        assert res["confidence"] == "high"

    def test_classify_file_role_c_content(self):
        res = classify_file_role("app.c", source="int main(int argc, char** argv) { return 0; }")
        assert res["role"] == "Entry Point"
        assert res["confidence"] == "high"

    def test_classify_file_role_heuristic_stem(self):
        assert classify_file_role("user_service.py")["role"] == "Service"
        assert classify_file_role("order_controller.py")["role"] == "Controller"
        assert classify_file_role("product_repository.py")["role"] == "Repository"

    # ── Entry Point Detection ─────────────────────────────────────────────────

    def test_detect_entry_points_polyglot(self, tmp_path):
        (tmp_path / "main.py").write_text("if __name__ == '__main__': print('hi')")
        (tmp_path / "App.java").write_text("public class App { public static void main(String[] args) {} }")
        (tmp_path / "core.c").write_text("int main(void) { return 0; }")

        files = ["main.py", "App.java", "core.c"]
        eps = detect_entry_points(str(tmp_path), files)

        assert len(eps) == 3
        languages = {e["language"] for e in eps}
        assert languages == {"Python", "Java", "C"}
        for e in eps:
            assert e["confidence"] == "high"

    def test_detect_entry_points_filename_convention(self, tmp_path):
        (tmp_path / "app.py").write_text("# simple python file without main guard")
        eps = detect_entry_points(str(tmp_path), ["app.py"])
        assert len(eps) == 1
        assert eps[0]["confidence"] == "medium"

    # ── Artifact Detection ────────────────────────────────────────────────────

    def test_detect_project_artifacts(self, tmp_path):
        (tmp_path / "pom.xml").write_text("<project></project>")
        (tmp_path / "Dockerfile").write_text("FROM python:3.9")
        (tmp_path / "pytest.ini").write_text("[pytest]")

        arts = detect_project_artifacts(str(tmp_path))
        assert any(a["name"] == "Maven" for a in arts["build_tools"])
        assert any(a["name"] == "Docker" for a in arts["deployment"])
        assert any(a["name"] == "pytest" for a in arts["testing"])

    # ── Dependency Graph Extraction ───────────────────────────────────────────

    def test_build_dependency_graph_python(self, tmp_path):
        (tmp_path / "main.py").write_text("import helper\nfrom utils import do_something")
        (tmp_path / "helper.py").write_text("x = 1")
        (tmp_path / "utils.py").write_text("def do_something(): pass")

        source_files = ["main.py", "helper.py", "utils.py"]
        eps = [{"path": "main.py", "confidence": "high"}]
        graph = build_dependency_graph(str(tmp_path), source_files, eps)

        assert len(graph["nodes"]) == 3
        assert graph["local_relationships"] >= 2
        edges = {(e["source"], e["target"]) for e in graph["edges"]}
        assert ("main.py", "helper.py") in edges or ("main.py", "utils.py") in edges

    def test_build_dependency_graph_c_includes(self, tmp_path):
        (tmp_path / "main.c").write_text('#include "utils.h"\nint main() { return 0; }')
        (tmp_path / "utils.h").write_text("#pragma once\n")

        source_files = ["main.c", "utils.h"]
        eps = [{"path": "main.c", "confidence": "high"}]
        graph = build_dependency_graph(str(tmp_path), source_files, eps)

        assert len(graph["nodes"]) == 2
        assert ("main.c", "utils.h") in {(e["source"], e["target"]) for e in graph["edges"]}

    # ── Reading Path & Architectural Clues ────────────────────────────────────

    def test_generate_reading_path(self, tmp_path):
        (tmp_path / "README.md").write_text("# My App")
        (tmp_path / "requirements.txt").write_text("flask")
        (tmp_path / "main.py").write_text("if __name__ == '__main__': pass")

        imp_files = [
            {"path": "README.md", "name": "README.md", "role": "Documentation", "importance": "high"},
            {"path": "requirements.txt", "name": "requirements.txt", "role": "Dependency Manifest", "importance": "high"},
        ]
        eps = [{"path": "main.py", "confidence": "high"}]
        dirs = [{"path": "services/", "role": "Service"}]

        path = generate_reading_path(str(tmp_path), imp_files, dirs, eps)
        assert len(path) >= 3
        assert path[0]["path"] == "README.md"
        assert path[1]["path"] == "requirements.txt"
        assert path[2]["path"] == "main.py"

    def test_detect_architectural_clues_layered(self):
        dirs = [
            {"name": "controllers", "role": "Controller"},
            {"name": "services", "role": "Service"},
            {"name": "repositories", "role": "Repository"},
            {"name": "models", "role": "Data Model"},
        ]
        clues = detect_architectural_clues(dirs)
        assert len(clues) >= 1
        assert "Layered Architecture" in clues[0]["pattern"]

    def test_detect_subprojects(self, tmp_path):
        sub1 = tmp_path / "service_a"
        sub1.mkdir()
        (sub1 / "pom.xml").write_text("<project></project>")

        sub2 = tmp_path / "service_b"
        sub2.mkdir()
        (sub2 / "build.gradle").write_text("// gradle")

        subprojects = detect_subprojects(str(tmp_path))
        assert len(subprojects) == 2
        paths = {s["path"] for s in subprojects}
        assert "service_a/" in paths
        assert "service_b/" in paths

    # ── Polyglot Overview Orchestrator ────────────────────────────────────────

    def test_analyze_repository_overview_polyglot(self, tmp_path):
        (tmp_path / "README.md").write_text("# Polyglot System")
        (tmp_path / "requirements.txt").write_text("requests\npytest\n")
        (tmp_path / "pom.xml").write_text("<project></project>")

        src = tmp_path / "src"
        src.mkdir()
        (src / "main.py").write_text("import helper\nif __name__ == '__main__': pass")
        (src / "helper.py").write_text("x = 1")
        (src / "App.java").write_text("public class App { public static void main(String[] args) {} }")
        (src / "core.c").write_text('#include "core.h"\nint main() { return 0; }')
        (src / "core.h").write_text("#pragma once")

        source_files = [
            "src/main.py",
            "src/helper.py",
            "src/App.java",
            "src/core.c",
            "src/core.h",
        ]

        overview = analyze_repository_overview(str(tmp_path), "polyglot_demo", source_files)

        assert overview["repository"]["name"] == "polyglot_demo"
        assert overview["repository"]["source_files"] == 5
        assert overview["repository"]["is_polyglot"] is True
        assert set(overview["repository"]["detected_languages"]) == {"Python", "Java", "C"}

        assert len(overview["entry_points"]) == 3
        assert len(overview["recommended_reading_path"]) >= 4
        assert len(overview["technologies"]) >= 3
