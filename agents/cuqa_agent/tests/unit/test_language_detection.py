"""
unit/test_language_detection.py
--------------------------------
Comprehensive unit tests for detect_language().
"""

import pytest
# pyrefly: ignore [missing-import]
from ast_parser import detect_language, SUPPORTED_LANGUAGES


@pytest.mark.unit
class TestLanguageDetection:
    @pytest.mark.parametrize("filename, expected", [
        ("main.py", "python"),
        ("Main.java", "java"),
        ("main.c", "c"),
        ("utils.h", "c"),
    ])
    def test_positive_cases(self, filename: str, expected: str):
        assert detect_language(filename) == expected

    @pytest.mark.parametrize("filename, expected", [
        ("MAIN.PY", "python"),
        ("Example.JAVA", "java"),
        ("FILE.C", "c"),
        ("HEADER.H", "c"),
    ])
    def test_case_insensitivity(self, filename: str, expected: str):
        assert detect_language(filename) == expected

    @pytest.mark.parametrize("filename", [
        "file.cpp",
        "file.js",
        "file.ts",
        "file.cs",
        "file.go",
        "file.rb",
        "file.php",
        "file.txt",
        "file.xml",
        "file.json",
        "file.md",
        "file.class",
        "file.exe",
        "README",
        ".gitignore",
        "",
        "file_without_ext",
        "file.py.backup",
    ])
    def test_unsupported_and_unknown_files(self, filename: str):
        assert detect_language(filename) == "unknown"

    def test_multiple_dots(self):
        assert detect_language("archive.tar.gz.py") == "python"
        assert detect_language("my.component.test.java") == "java"
        assert detect_language("lib.v1.c") == "c"

    def test_path_with_folders(self):
        assert detect_language("folder.name/file.py") == "python"
        assert detect_language("src/main/java/App.java") == "java"
        assert detect_language("c_src/core/main.c") == "c"

    def test_unicode_and_long_filenames(self):
        assert detect_language("සිංහල_කේතය.py") == "python"
        assert detect_language("🎉_emoji_code.java") == "java"
        long_name = "a" * 300 + ".c"
        assert detect_language(long_name) == "c"

    def test_supported_languages_dict(self):
        assert ".py" in SUPPORTED_LANGUAGES
        assert ".java" in SUPPORTED_LANGUAGES
        assert ".c" in SUPPORTED_LANGUAGES
        assert ".h" in SUPPORTED_LANGUAGES
