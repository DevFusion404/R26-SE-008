"""
ast_parser.py
-------------
Language dispatcher for the CUQA Agent AST parsing pipeline.
Detects file language by extension and routes to the correct parser.

Supported languages:
  .py   → python_ast_parser  (built-in ast module)
  .java → java_ast_parser    (javalang library)
  .c    → c_ast_parser       (tree-sitter or regex fallback)
  .h    → c_ast_parser       (same as .c)
"""

import os
from typing import Union
from python_ast_parser import parse_python_file, parse_python_source
from java_ast_parser import parse_java_file, parse_java_source
from c_ast_parser import parse_c_file, parse_c_source

SUPPORTED_LANGUAGES = {
    ".py": "python",
    ".java": "java",
    ".c": "c",
    ".h": "c",
}


def detect_language(filename: str) -> str:
    """Return the language identifier for a file, or 'unknown'."""
    ext = os.path.splitext(filename)[-1].lower()
    return SUPPORTED_LANGUAGES.get(ext, "unknown")


def parse_file(file_path: str) -> dict:
    """
    Parse a source file and return the CUQA AST JSON.

    Dispatches to the correct language parser based on file extension.

    Args:
        file_path: Path to the source file.

    Returns:
        CUQA AST JSON dict.
    """
    ext = os.path.splitext(file_path)[-1].lower()
    lang = SUPPORTED_LANGUAGES.get(ext)

    if lang == "python":
        return parse_python_file(file_path)
    elif lang == "java":
        return parse_java_file(file_path)
    elif lang == "c":
        return parse_c_file(file_path)
    else:
        return {
            "file": os.path.basename(file_path),
            "language": "unknown",
            "error": f"Unsupported file type: '{ext}'. Supported: {list(SUPPORTED_LANGUAGES.keys())}",
            "ast": {},
        }


def parse_source(source: str, filename: str) -> dict:
    """
    Parse source code provided as a string.

    Args:
        source:   Raw source code.
        filename: Original filename (used for language detection).

    Returns:
        CUQA AST JSON dict.
    """
    ext = os.path.splitext(filename)[-1].lower()
    lang = SUPPORTED_LANGUAGES.get(ext)

    if lang == "python":
        return parse_python_source(source, filename)
    elif lang == "java":
        return parse_java_source(source, filename)
    elif lang == "c":
        return parse_c_source(source, filename)
    else:
        return {
            "file": filename,
            "language": "unknown",
            "error": f"Unsupported file type: '{ext}'",
            "ast": {},
        }
