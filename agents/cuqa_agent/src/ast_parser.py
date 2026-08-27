"""
ast_parser.py
-------------
Language Dispatcher Module for the CUQA (Code Understanding & Quality Assessment) Agent.

===============================================================================
SPECIAL FUNCTION & ARCHITECTURAL OVERVIEW FOR CODE VIVA / PRESENTATION:
===============================================================================
1. PURPOSE:
   This module serves as the central router/dispatcher in the CUQA parsing pipeline.
   Instead of forcing the API or caller to know which parser to invoke, `ast_parser.py`
   inspects file extensions and dynamically routes the source code to the appropriate
   language-specific parser (`python_ast_parser`, `java_ast_parser`, `c_ast_parser`).

2. KEY DESIGN PATTERN — Strategy / Dispatcher Pattern:
   - Encapsulates parser selection behind a unified API (`parse_file`, `parse_source`).
   - Standardizes output structure into a uniform CUQA AST JSON schema regardless
     of the underlying parsing engine (Python `ast`, `javalang`, `tree-sitter`, or `regex`).

3. SUPPORTED LANGUAGES & ROUTING:
   - `.py`       -> Python Parser (`python_ast_parser.py` using Python's native `ast`)
   - `.java`     -> Java Parser   (`java_ast_parser.py` using `javalang` AST visitor)
   - `.c`, `.h`  -> C Parser      (`c_ast_parser.py` using `tree-sitter` with Regex fallback)
===============================================================================
"""

import os
from typing import Union
# Import language-specific parsing functions from sibling modules
from python_ast_parser import parse_python_file, parse_python_source
from java_ast_parser import parse_java_file, parse_java_source
from c_ast_parser import parse_c_file, parse_c_source

# ---------------------------------------------------------------------------
# Language Mapping Registry
# Maps file extensions (lowercase) to canonical language identifiers.
# ---------------------------------------------------------------------------
SUPPORTED_LANGUAGES = {
    ".py": "python",
    ".java": "java",
    ".c": "c",
    ".h": "c",
}


def detect_language(filename: str) -> str:
    """
    Detect the programming language of a file based on its extension.

    -------------------------------------------------------------------------
    VIVA/INTERVIEW NOTE: How Language Detection Works:
    1. Uses `os.path.splitext(filename)` to extract the file extension (e.g. '.py').
    2. Converts extension to lowercase to handle cases like '.JAVA' or '.C'.
    3. Looks up the extension in `SUPPORTED_LANGUAGES`.
    4. Returns 'unknown' if the extension is not supported (prevents runtime KeyErrors).
    -------------------------------------------------------------------------

    Args:
        filename (str): Name or path of the file (e.g., 'main.py' or '/path/to/App.java').

    Returns:
        str: Language identifier ('python', 'java', 'c', or 'unknown').
    """
    ext = os.path.splitext(filename)[-1].lower()
    return SUPPORTED_LANGUAGES.get(ext, "unknown")


def parse_file(file_path: str) -> dict:
    """
    Parse a source code file on disk and return a standardized CUQA AST JSON dict.

    -------------------------------------------------------------------------
    VIVA/INTERVIEW NOTE: Central Dispatch Logic:
    - Extracts extension from file_path.
    - Dispatches execution to the corresponding language-specific parser function.
    - If unsupported, returns a graceful error JSON structure instead of crashing.
      This ensures the pipeline remains robust when scanning mixed/polyglot repos.
    -------------------------------------------------------------------------

    Args:
        file_path (str): Path to the source file on disk.

    Returns:
        dict: Standardized CUQA AST JSON containing keys:
              - 'file': filename
              - 'language': language name
              - 'ast': AST node hierarchy
              - 'error': error message (if parsing failed or file type unsupported)
    """
    ext = os.path.splitext(file_path)[-1].lower()
    lang = SUPPORTED_LANGUAGES.get(ext)

    # Route file to appropriate parser implementation
    if lang == "python":
        return parse_python_file(file_path)
    elif lang == "java":
        return parse_java_file(file_path)
    elif lang == "c":
        return parse_c_file(file_path)
    else:
        # Graceful fallback error dictionary for unsupported file formats
        return {
            "file": os.path.basename(file_path),
            "language": "unknown",
            "error": f"Unsupported file type: '{ext}'. Supported: {list(SUPPORTED_LANGUAGES.keys())}",
            "ast": {},
        }


def parse_source(source: str, filename: str) -> dict:
    """
    Parse raw source code strings directly without requiring disk reading.

    -------------------------------------------------------------------------
    VIVA/INTERVIEW NOTE: Why String Parsing is Needed:
    - Useful when processing files loaded into memory, API payloads, or temporary buffers.
    - Uses `filename` solely to detect language via extension.
    -------------------------------------------------------------------------

    Args:
        source (str): Raw string content of the source code.
        filename (str): Original filename used for extension detection (e.g. 'script.py').

    Returns:
        dict: Standardized CUQA AST JSON dictionary.
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

