"""
repository_understanding.py
---------------------------
CUQA Repository Understanding Module.

Transforms a raw legacy source repository into a structured, beginner-friendly
understanding model using purely static analysis — no external LLMs or APIs required.

Answers the newcomer questions:
  1. What is this repository?
  2. What technologies does it use?
  3. How is it organized?
  4. Where does execution start?
  5. Where should I read first?
  6. How are modules connected?
  7. Which files are structurally central?
  8. Which files are maintenance hotspots?

===============================================================================
RESEARCH NOTE (R26-SE-008):
  This module fulfils the Code Understanding responsibility of the CUQA Agent,
  distinct from Quality Assessment (report_generator.py).
  The produced structured representation serves as orientation data for developers
  who have never previously encountered the repository.
===============================================================================
"""

from __future__ import annotations

import os
import re
import ast as pyast
from pathlib import Path
from typing import Optional
from collections import defaultdict

# ---------------------------------------------------------------------------
# Constants: Ignored directories (generated / vendor / IDE artefacts)
# ---------------------------------------------------------------------------

IGNORED_DIRS: frozenset[str] = frozenset({
    ".git", ".github", "node_modules", "__pycache__",
    ".venv", "venv", "env", ".env",
    "target", "build", "dist", "out",
    "coverage", ".coverage", "htmlcov",
    ".idea", ".vscode", ".eclipse",
    ".mypy_cache", ".pytest_cache", ".tox",
    "bin", "obj",  # C# / MSBuild artefacts that may appear
})

SUPPORTED_EXTENSIONS: frozenset[str] = frozenset({".py", ".java", ".c", ".h"})

_EXT_TO_LANG: dict[str, str] = {
    ".py": "Python",
    ".java": "Java",
    ".c": "C",
    ".h": "C",
}

# ---------------------------------------------------------------------------
# Directory role knowledge base
# ---------------------------------------------------------------------------

# Mapping: lower-case dir name fragment → (role, description, confidence)
_DIR_ROLE_MAP: dict[str, tuple[str, str, str]] = {
    # Source roots
    "src":          ("Source",       "Main application source code.",                  "high"),
    "source":       ("Source",       "Main application source code.",                  "high"),
    "lib":          ("Library",      "Shared library or utility code.",                "high"),
    "libs":         ("Library",      "Shared library or utility code.",                "high"),
    "core":         ("Core",         "Core application logic.",                        "high"),
    "main":         ("Source",       "Main application source.",                       "medium"),
    "app":          ("Application",  "Application entry and orchestration code.",      "medium"),

    # Layered architecture
    "controller":   ("Controller",   "Request handling and routing logic.",            "high"),
    "controllers":  ("Controller",   "Request handling and routing logic.",            "high"),
    "handler":      ("Controller",   "Request / event handling logic.",                "high"),
    "handlers":     ("Controller",   "Request / event handling logic.",                "high"),
    "route":        ("Controller",   "API route definitions.",                         "high"),
    "routes":       ("Controller",   "API route definitions.",                         "high"),
    "api":          ("API",          "Public API definitions or endpoints.",           "high"),

    "service":      ("Service",      "Business / service logic.",                      "high"),
    "services":     ("Service",      "Business / service logic.",                      "high"),
    "usecase":      ("Service",      "Application use-case implementations.",          "high"),
    "usecases":     ("Service",      "Application use-case implementations.",          "high"),
    "business":     ("Service",      "Business domain logic.",                         "high"),
    "logic":        ("Service",      "Application logic.",                             "medium"),
    "domain":       ("Domain",       "Domain / business-object definitions.",          "high"),

    "repository":   ("Repository",   "Data access / persistence layer.",              "high"),
    "repositories": ("Repository",   "Data access / persistence layer.",              "high"),
    "repo":         ("Repository",   "Data access or repository pattern classes.",     "medium"),
    "dao":          ("Repository",   "Data Access Object implementations.",            "high"),
    "persistence":  ("Repository",   "Persistence / storage layer.",                  "high"),
    "store":        ("Repository",   "Data store or state management.",               "medium"),
    "database":     ("Repository",   "Database interaction code.",                    "high"),
    "db":           ("Repository",   "Database interaction code.",                    "high"),

    "model":        ("Data Model",   "Data / domain model definitions.",              "high"),
    "models":       ("Data Model",   "Data / domain model definitions.",              "high"),
    "entity":       ("Data Model",   "Entity / ORM model classes.",                   "high"),
    "entities":     ("Data Model",   "Entity / ORM model classes.",                   "high"),
    "schema":       ("Schema",       "Data schema or validation definitions.",         "high"),
    "schemas":      ("Schema",       "Data schema or validation definitions.",         "high"),
    "dto":          ("Data Model",   "Data Transfer Objects.",                         "high"),
    "dtos":         ("Data Model",   "Data Transfer Objects.",                         "high"),

    "util":         ("Utility",      "General-purpose utility / helper functions.",   "high"),
    "utils":        ("Utility",      "General-purpose utility / helper functions.",   "high"),
    "helper":       ("Utility",      "Helper functions and shared utilities.",         "high"),
    "helpers":      ("Utility",      "Helper functions and shared utilities.",         "high"),
    "common":       ("Utility",      "Shared / common code used across modules.",     "medium"),
    "shared":       ("Utility",      "Shared code between modules.",                  "medium"),
    "support":      ("Utility",      "Support utilities.",                            "medium"),
    "tools":        ("Utility",      "Developer or build tools.",                     "medium"),

    # Tests
    "test":         ("Tests",        "Automated tests verifying application behaviour.", "high"),
    "tests":        ("Tests",        "Automated tests verifying application behaviour.", "high"),
    "spec":         ("Tests",        "Specification / test files.",                   "high"),
    "specs":        ("Tests",        "Specification / test files.",                   "high"),
    "__tests__":    ("Tests",        "Test suite directory.",                         "high"),

    # Configuration
    "config":       ("Configuration","Application configuration files.",              "high"),
    "configs":      ("Configuration","Application configuration files.",              "high"),
    "conf":         ("Configuration","Application configuration files.",              "high"),
    "settings":     ("Configuration","Application settings.",                         "high"),
    "properties":   ("Configuration","Configuration property files.",                 "medium"),
    "env":          ("Configuration","Environment-specific configuration.",           "medium"),

    # Documentation
    "doc":          ("Documentation","Project / API documentation.",                  "high"),
    "docs":         ("Documentation","Project / API documentation.",                  "high"),
    "wiki":         ("Documentation","Project wiki.",                                  "medium"),

    # Scripts
    "script":       ("Scripts",      "Build, deployment or automation scripts.",      "high"),
    "scripts":      ("Scripts",      "Build, deployment or automation scripts.",      "high"),
    "bin":          ("Scripts",      "Executable or binary scripts.",                 "medium"),

    # C-specific
    "include":      ("Headers",      "C/C++ header / interface declarations.",         "high"),
    "includes":     ("Headers",      "C/C++ header / interface declarations.",         "high"),
    "header":       ("Headers",      "C/C++ header files.",                           "high"),
    "headers":      ("Headers",      "C/C++ header files.",                           "high"),

    # Infrastructure
    "deploy":       ("Deployment",   "Deployment configuration or scripts.",          "high"),
    "deployment":   ("Deployment",   "Deployment configuration or scripts.",          "high"),
    "infra":        ("Infrastructure","Infrastructure-as-code definitions.",          "high"),
    "infrastructure":("Infrastructure","Infrastructure configuration.",              "high"),
    "k8s":          ("Deployment",   "Kubernetes manifests.",                         "high"),
    "kubernetes":   ("Deployment",   "Kubernetes manifests.",                         "high"),
    "docker":       ("Deployment",   "Docker configuration.",                         "high"),

    # Middleware / plugins
    "middleware":   ("Middleware",   "Middleware / interceptor components.",           "high"),
    "plugin":       ("Plugin",       "Plugin / extension implementations.",           "medium"),
    "plugins":      ("Plugin",       "Plugin / extension implementations.",           "medium"),
    "extension":    ("Plugin",       "Extension code.",                               "medium"),
    "extensions":   ("Plugin",       "Extension code.",                               "medium"),

    # Security
    "auth":         ("Auth",         "Authentication and authorisation logic.",        "high"),
    "security":     ("Auth",         "Security and authentication logic.",             "high"),
    "oauth":        ("Auth",         "OAuth / SSO implementation.",                   "high"),

    # Migrations
    "migration":    ("Migration",    "Database schema or data migrations.",            "high"),
    "migrations":   ("Migration",    "Database schema or data migrations.",            "high"),
    "flyway":       ("Migration",    "Flyway database migrations.",                    "high"),
    "liquibase":    ("Migration",    "Liquibase database migrations.",                 "high"),
}

# ---------------------------------------------------------------------------
# File role knowledge base
# ---------------------------------------------------------------------------

_FILE_ROLE_MAP: dict[str, tuple[str, str, str]] = {
    # Documentation
    "readme.md":          ("Documentation",       "Project overview and usage guide.",                "high"),
    "readme.txt":         ("Documentation",       "Project overview and usage guide.",                "high"),
    "readme.rst":         ("Documentation",       "Project overview in reStructuredText.",            "high"),
    "changelog.md":       ("Documentation",       "Describes changes between versions.",              "medium"),
    "changelog.txt":      ("Documentation",       "Describes changes between versions.",              "medium"),
    "contributing.md":    ("Documentation",       "Contribution guidelines.",                         "medium"),
    "license":            ("License",             "Software license terms.",                          "medium"),
    "license.md":         ("License",             "Software license terms.",                          "medium"),
    "license.txt":        ("License",             "Software license terms.",                          "medium"),
    "authors.md":         ("Documentation",       "Authors and contributors list.",                   "low"),
    "authors.txt":        ("Documentation",       "Authors and contributors list.",                   "low"),

    # Python dependency/project files
    "requirements.txt":   ("Dependency Manifest", "Python package dependencies (pip).",               "high"),
    "requirements-dev.txt":("Dependency Manifest","Development Python dependencies.",                 "medium"),
    "requirements-test.txt":("Dependency Manifest","Test Python dependencies.",                       "medium"),
    "pyproject.toml":     ("Build Configuration", "Python build system and project metadata.",        "high"),
    "setup.py":           ("Build Configuration", "Python package setup script.",                     "high"),
    "setup.cfg":          ("Build Configuration", "Python package configuration.",                    "medium"),
    "pipfile":            ("Dependency Manifest", "Pipenv dependency specification.",                  "high"),
    "pipfile.lock":       ("Dependency Manifest", "Pipenv locked dependency versions.",               "medium"),
    "poetry.lock":        ("Dependency Manifest", "Poetry locked dependency versions.",               "medium"),
    "tox.ini":            ("Build Configuration", "Tox test environment configuration.",               "medium"),
    "pytest.ini":         ("Build Configuration", "Pytest configuration.",                            "medium"),
    ".flake8":            ("Build Configuration", "Flake8 linting configuration.",                    "low"),
    "mypy.ini":           ("Build Configuration", "Mypy type-checking configuration.",                "low"),

    # Java build files
    "pom.xml":            ("Build Configuration", "Maven project and dependency configuration.",      "high"),
    "build.gradle":       ("Build Configuration", "Gradle build script.",                            "high"),
    "build.gradle.kts":   ("Build Configuration", "Gradle build script (Kotlin DSL).",               "high"),
    "settings.gradle":    ("Build Configuration", "Gradle multi-project settings.",                  "medium"),
    "settings.gradle.kts":("Build Configuration", "Gradle settings (Kotlin DSL).",                   "medium"),
    "gradlew":            ("Build Configuration", "Gradle wrapper script.",                          "medium"),
    "gradlew.bat":        ("Build Configuration", "Gradle wrapper script (Windows).",                "low"),

    # C build files
    "makefile":           ("Build Configuration", "Make build configuration.",                       "high"),
    "gnumakefile":        ("Build Configuration", "GNU Make build configuration.",                   "high"),
    "cmakelists.txt":     ("Build Configuration", "CMake build configuration.",                      "high"),
    "configure":          ("Build Configuration", "Autotools configure script.",                     "medium"),
    "configure.ac":       ("Build Configuration", "Autotools configure.ac template.",               "medium"),
    "autogen.sh":         ("Build Configuration", "Autotools generation script.",                    "low"),
    "meson.build":        ("Build Configuration", "Meson build configuration.",                      "high"),
    "conanfile.txt":      ("Dependency Manifest", "Conan C/C++ dependency manifest.",                "high"),
    "conanfile.py":       ("Dependency Manifest", "Conan C/C++ dependency manifest (Python).",       "high"),

    # Docker / container
    "dockerfile":         ("Deployment",          "Docker container build instructions.",             "high"),
    "docker-compose.yml": ("Deployment",          "Docker Compose multi-service configuration.",     "high"),
    "docker-compose.yaml":("Deployment",          "Docker Compose multi-service configuration.",     "high"),
    ".dockerignore":      ("Deployment",          "Files excluded from Docker build context.",       "low"),

    # Environment
    ".env":               ("Configuration",       "Environment variable definitions.",               "medium"),
    ".env.example":       ("Configuration",       "Example environment variable template.",           "high"),
    ".env.sample":        ("Configuration",       "Sample environment configuration.",               "medium"),

    # CI/CD
    ".travis.yml":        ("CI/CD",               "Travis CI pipeline configuration.",               "medium"),
    "jenkinsfile":        ("CI/CD",               "Jenkins pipeline definition.",                    "medium"),
    "appveyor.yml":       ("CI/CD",               "AppVeyor CI configuration.",                     "low"),
    "azure-pipelines.yml":("CI/CD",               "Azure Pipelines CI/CD configuration.",           "medium"),

    # General config
    ".gitignore":         ("Configuration",       "Files excluded from Git version control.",        "low"),
    ".gitattributes":     ("Configuration",       "Git file attribute configuration.",               "low"),
    "sonar-project.properties":("Build Configuration","SonarQube project configuration.",          "low"),
}

# Python entry-point filename signals
_PYTHON_ENTRY_NAMES: frozenset[str] = frozenset({
    "main.py", "app.py", "run.py", "server.py",
    "manage.py", "cli.py", "wsgi.py", "asgi.py",
    "start.py", "boot.py", "launcher.py", "application.py",
})

# Java entry-point pattern
_JAVA_MAIN_RE = re.compile(
    r"public\s+static\s+void\s+main\s*\(\s*String",
    re.MULTILINE,
)

# C entry-point pattern (int main or void main)
_C_MAIN_RE = re.compile(
    r"\b(?:int|void)\s+main\s*\([^)]*\)",
    re.MULTILINE,
)

# Python __main__ guard
_PY_MAIN_RE = re.compile(
    r'if\s+__name__\s*==\s*["\']__main__["\']',
    re.MULTILINE,
)

# Import extractors
_PY_IMPORT_RE = re.compile(
    r'^(?:import\s+([\w.]+)|from\s+([\w.]+)\s+import)',
    re.MULTILINE,
)
_JAVA_IMPORT_RE = re.compile(
    r'^import\s+([\w.]+);',
    re.MULTILINE,
)
_C_LOCAL_INCLUDE_RE = re.compile(
    r'^#include\s+"([^"]+)"',
    re.MULTILINE,
)

# Build / tooling artifact matchers
_ARTIFACT_PATTERNS: list[tuple[str, str, str, str]] = [
    # (glob-like filename, category, tool_name, description)
    # Build tools
    ("pom.xml",             "build_tools",        "Maven",          "Maven build system"),
    ("build.gradle",        "build_tools",        "Gradle",         "Gradle build system"),
    ("build.gradle.kts",    "build_tools",        "Gradle",         "Gradle build system (Kotlin DSL)"),
    ("makefile",            "build_tools",        "Make",           "Make build system"),
    ("gnumakefile",         "build_tools",        "Make",           "GNU Make build system"),
    ("cmakelists.txt",      "build_tools",        "CMake",          "CMake build system"),
    ("meson.build",         "build_tools",        "Meson",          "Meson build system"),
    ("configure",           "build_tools",        "Autotools",      "Autotools build system"),
    ("configure.ac",        "build_tools",        "Autotools",      "Autotools build system"),
    ("gradlew",             "build_tools",        "Gradle Wrapper", "Gradle wrapper script"),
    ("autogen.sh",          "build_tools",        "Autotools",      "Autotools generator"),

    # Dependency managers
    ("requirements.txt",    "dependency_managers","pip",            "Python pip dependencies"),
    ("requirements-dev.txt","dependency_managers","pip",            "Python pip dev dependencies"),
    ("pipfile",             "dependency_managers","Pipenv",         "Pipenv dependency manager"),
    ("pyproject.toml",      "dependency_managers","pip/Poetry",     "Python project dependencies"),
    ("setup.py",            "dependency_managers","setuptools",     "Python setuptools"),
    ("conanfile.txt",       "dependency_managers","Conan",          "C/C++ Conan package manager"),
    ("conanfile.py",        "dependency_managers","Conan",          "C/C++ Conan package manager"),

    # Testing
    ("pytest.ini",          "testing",            "pytest",         "pytest configuration"),
    ("setup.cfg",           "testing",            "pytest",         "May contain pytest config"),
    ("tox.ini",             "testing",            "tox",            "tox test runner"),
    ("testng.xml",          "testing",            "TestNG",         "TestNG test configuration"),

    # Deployment / container
    ("dockerfile",          "deployment",         "Docker",         "Docker container"),
    ("docker-compose.yml",  "deployment",         "Docker Compose", "Docker Compose"),
    ("docker-compose.yaml", "deployment",         "Docker Compose", "Docker Compose"),
    ("vagrantfile",         "deployment",         "Vagrant",        "Vagrant VM"),
    ("kubernetes",          "deployment",         "Kubernetes",     "Kubernetes (directory)"),
    ("k8s",                 "deployment",         "Kubernetes",     "Kubernetes manifests"),

    # CI/CD
    (".travis.yml",         "ci_cd",              "Travis CI",      "Travis CI pipeline"),
    ("jenkinsfile",         "ci_cd",              "Jenkins",        "Jenkins pipeline"),
    ("appveyor.yml",        "ci_cd",              "AppVeyor",       "AppVeyor CI"),
    ("azure-pipelines.yml", "ci_cd",              "Azure Pipelines","Azure Pipelines CI"),
]


# ---------------------------------------------------------------------------
# Template descriptions for file roles (for deterministic natural language)
# ---------------------------------------------------------------------------

_ROLE_TEMPLATES: dict[str, str] = {
    "Documentation":       "Contains project documentation.",
    "Dependency Manifest": "Lists external packages required by the application.",
    "Build Configuration": "Configures the build system or project tooling.",
    "Entry Point":         "Application execution begins here.",
    "Application Source":  "Primary application source code.",
    "Service":             "Service / business logic.",
    "Data Model":          "Data or domain model definitions.",
    "Controller":          "Request handling or routing logic.",
    "Repository":          "Data access / persistence layer.",
    "Utility":             "General-purpose utilities or helpers.",
    "Tests":               "Automated tests.",
    "Configuration":       "Application or environment configuration.",
    "CI/CD":               "Continuous integration / deployment pipeline.",
    "Deployment":          "Container or infrastructure deployment configuration.",
    "License":             "Software license.",
    "Header / Interface":  "C/C++ header providing declarations.",
    "Schema":              "Data schema or validation definitions.",
    "Migration":           "Database migration scripts.",
    "Auth":                "Authentication / authorisation logic.",
    "API":                 "Public API definitions.",
    "Plugin":              "Extension or plugin code.",
    "Middleware":          "Middleware / interceptor logic.",
    "Domain":              "Domain business-object definitions.",
    "Infrastructure":      "Infrastructure-as-code.",
    "Unknown":             "Purpose not determined from static analysis.",
}

# Architectural layer name → canonical name
_ARCH_LAYERS: list[tuple[str, list[str]]] = [
    ("Controller / Presentation Layer", ["controller", "controllers", "handler", "handlers", "route", "routes", "api"]),
    ("Service / Business Logic Layer",  ["service", "services", "usecase", "usecases", "business", "logic", "domain"]),
    ("Repository / Persistence Layer",  ["repository", "repositories", "dao", "persistence", "store", "database", "db"]),
    ("Data Model / Entity Layer",       ["model", "models", "entity", "entities", "dto", "dtos", "schema", "schemas"]),
]


# ===========================================================================
# PUBLIC ANALYSIS FUNCTIONS
# ===========================================================================

def count_lines_of_code(root: str, source_files: list[str], cap: int = 1000) -> dict:
    """
    Count lines of code per language and total, reading at most `cap` files.

    Lines are raw (not stripped) to stay consistent with convention.
    Encoding errors are silently replaced so malformed legacy files do not abort.

    Returns:
        {
            "total": int,
            "by_language": { "Python": int, "Java": int, "C": int },
            "capped": bool,   # True if more files exist than cap allows
        }
    """
    by_lang: dict[str, int] = {}
    total = 0
    processed = 0

    for rel in source_files[:cap]:
        full = os.path.join(root, rel)
        ext = os.path.splitext(rel)[-1].lower()
        lang = _EXT_TO_LANG.get(ext, "Unknown")
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            count = len(lines)
        except OSError:
            count = 0
        by_lang[lang] = by_lang.get(lang, 0) + count
        total += count
        processed += 1

    return {
        "total": total,
        "by_language": by_lang,
        "capped": len(source_files) > cap,
    }


def count_directories(root: str) -> int:
    """
    Count non-ignored directories inside the repository root (recursive).
    """
    count = 0
    for dirpath, dirnames, _ in os.walk(root):
        # Prune ignored dirs in-place so os.walk does not descend into them
        dirnames[:] = [
            d for d in dirnames
            if d.lower() not in IGNORED_DIRS and not d.startswith(".")
        ]
        count += len(dirnames)
    return count


def detect_project_artifacts(root: str) -> dict:
    """
    Scan the repository root (and common sub-directories) for well-known
    build / dependency / CI / deployment artefact files.

    Returns a dict with categories:
        build_tools, dependency_managers, testing, deployment, ci_cd
    Each is a list of { name, evidence, description }.
    """
    result: dict[str, list[dict]] = {
        "build_tools": [],
        "dependency_managers": [],
        "testing": [],
        "deployment": [],
        "ci_cd": [],
    }
    seen: dict[str, set] = {k: set() for k in result}

    # Collect candidate directories to scan
    dirs_to_scan = [root]
    try:
        for entry in os.scandir(root):
            if entry.is_dir() and entry.name.lower() not in IGNORED_DIRS:
                dirs_to_scan.append(entry.path)
    except OSError:
        pass

    # Scan for .github/workflows specifically
    gh_workflows = os.path.join(root, ".github", "workflows")
    if os.path.isdir(gh_workflows):
        # Collect workflow files as CI/CD evidence
        workflow_files = []
        try:
            workflow_files = [
                f for f in os.listdir(gh_workflows)
                if f.endswith((".yml", ".yaml"))
            ]
        except OSError:
            pass
        if workflow_files and "GitHub Actions" not in seen["ci_cd"]:
            seen["ci_cd"].add("GitHub Actions")
            result["ci_cd"].append({
                "name": "GitHub Actions",
                "evidence": [f".github/workflows/{f}" for f in workflow_files[:3]],
                "description": "GitHub Actions CI/CD workflows",
            })

    # Walk root-level and one level deep for artefact files
    for scan_dir in dirs_to_scan:
        try:
            entries = {e.name.lower(): e.name for e in os.scandir(scan_dir) if e.is_file()}
        except OSError:
            entries = {}

        for pattern, category, tool_name, description in _ARTIFACT_PATTERNS:
            if pattern in entries and tool_name not in seen[category]:
                rel_path = os.path.relpath(
                    os.path.join(scan_dir, entries[pattern]), root
                ).replace("\\", "/")
                seen[category].add(tool_name)
                result[category].append({
                    "name": tool_name,
                    "evidence": [rel_path],
                    "description": description,
                })

    # Check for pytest test files (evidence for pytest even without pytest.ini)
    if "pytest" not in seen["testing"]:
        _check_pytest_by_files(root, result, seen)

    return result


def _check_pytest_by_files(root: str, result: dict, seen: dict) -> None:
    """Add pytest to testing if test_*.py or *_test.py files exist at top level."""
    try:
        for entry in os.scandir(root):
            name = entry.name.lower()
            if entry.is_file() and (
                name.startswith("test_") or name.endswith("_test.py")
            ) and name.endswith(".py"):
                seen["testing"].add("pytest")
                result["testing"].append({
                    "name": "pytest",
                    "evidence": [entry.name],
                    "description": "pytest test files detected",
                })
                return
    except OSError:
        pass


def classify_directory_role(
    dir_name: str,
    contents: list[str],
    parent_path: str = "",
) -> dict:
    """
    Classify a directory's role based on its name and contents.

    Args:
        dir_name:    Name of the directory (e.g., "services").
        contents:    List of filenames inside the directory.
        parent_path: Relative path from repo root (used for evidence).

    Returns:
        { role, description, confidence, evidence }
    """
    key = dir_name.lower()
    evidence: list[str] = []

    if key in _DIR_ROLE_MAP:
        role, description, confidence = _DIR_ROLE_MAP[key]
        evidence.append(f"Directory name '{dir_name}' matches known {role} pattern")
        return {
            "role": role,
            "description": description,
            "confidence": confidence,
            "evidence": evidence,
        }

    # Partial-match on known keys (e.g., "user_services" contains "service")
    for fragment, (role, description, confidence) in _DIR_ROLE_MAP.items():
        if fragment in key:
            evidence.append(f"Directory name '{dir_name}' contains '{fragment}' (partial match)")
            return {
                "role": role,
                "description": description,
                "confidence": "medium" if confidence == "high" else confidence,
                "evidence": evidence,
            }

    # Content-based inference
    if contents:
        content_lower = [c.lower() for c in contents]
        if any(n.endswith(".py") for n in content_lower):
            if any(n.startswith("test_") or n.endswith("_test.py") for n in content_lower):
                return {
                    "role": "Tests",
                    "description": "Automated tests verifying application behaviour.",
                    "confidence": "high",
                    "evidence": [f"Contains test_*.py or *_test.py files"],
                }
        if any(n.endswith(".h") for n in content_lower):
            return {
                "role": "Headers",
                "description": "C/C++ header / interface declarations.",
                "confidence": "medium",
                "evidence": ["Contains .h header files"],
            }

    return {
        "role": "Unknown",
        "description": "Purpose not determined from static analysis.",
        "confidence": "low",
        "evidence": [f"Directory name '{dir_name}' does not match known patterns"],
    }


def classify_file_role(file_path: str, source: str = "") -> dict:
    """
    Classify a file's role based on its name and optionally source content.

    Args:
        file_path: Relative or absolute file path.
        source:    Optional file content for content-based analysis.

    Returns:
        { role, reason, importance, confidence }
    """
    filename = os.path.basename(file_path).lower()
    ext = os.path.splitext(filename)[-1].lower()

    # Check exact filename match in knowledge base
    if filename in _FILE_ROLE_MAP:
        role, reason, importance = _FILE_ROLE_MAP[filename]
        return {
            "role": role,
            "reason": reason,
            "importance": importance,
            "confidence": "high",
        }

    # Header files
    if ext == ".h":
        return {
            "role": "Header / Interface",
            "reason": "C/C++ header file providing declarations and interface definitions.",
            "importance": "medium",
            "confidence": "high",
        }

    # Content-based for Python files
    if ext == ".py" and source:
        if _PY_MAIN_RE.search(source):
            return {
                "role": "Entry Point",
                "reason": "Contains if __name__ == '__main__' guard.",
                "importance": "high",
                "confidence": "high",
            }
        if filename in _PYTHON_ENTRY_NAMES:
            return {
                "role": "Entry Point",
                "reason": f"Filename '{os.path.basename(file_path)}' matches common Python entry-point convention.",
                "importance": "high",
                "confidence": "medium",
            }

    # Java content-based
    if ext == ".java" and source:
        if _JAVA_MAIN_RE.search(source):
            return {
                "role": "Entry Point",
                "reason": "Contains public static void main(String[] args).",
                "importance": "high",
                "confidence": "high",
            }

    # C content-based
    if ext in (".c",) and source:
        if _C_MAIN_RE.search(source):
            return {
                "role": "Entry Point",
                "reason": "Contains int main(...) function.",
                "importance": "high",
                "confidence": "high",
            }

    # Name-fragment heuristics
    stem = os.path.splitext(filename)[0]
    if any(t in stem for t in ("test", "spec", "mock", "stub", "fixture")):
        return {
            "role": "Tests",
            "reason": f"Filename contains test-related term.",
            "importance": "medium",
            "confidence": "medium",
        }
    if any(t in stem for t in ("service", "svc")):
        return {
            "role": "Service",
            "reason": f"Filename '{stem}' suggests service/business logic.",
            "importance": "medium",
            "confidence": "medium",
        }
    if any(t in stem for t in ("controller", "ctrl", "handler", "route", "router")):
        return {
            "role": "Controller",
            "reason": f"Filename '{stem}' suggests request handling.",
            "importance": "medium",
            "confidence": "medium",
        }
    if any(t in stem for t in ("repository", "repo", "dao", "store")):
        return {
            "role": "Repository",
            "reason": f"Filename '{stem}' suggests data access layer.",
            "importance": "medium",
            "confidence": "medium",
        }
    if any(t in stem for t in ("model", "entity", "dto", "schema")):
        return {
            "role": "Data Model",
            "reason": f"Filename '{stem}' suggests data/domain model.",
            "importance": "medium",
            "confidence": "medium",
        }
    if any(t in stem for t in ("util", "utils", "helper", "helpers", "common", "shared")):
        return {
            "role": "Utility",
            "reason": f"Filename '{stem}' suggests utility/helper code.",
            "importance": "low",
            "confidence": "medium",
        }
    if any(t in stem for t in ("config", "setting", "conf", "property")):
        return {
            "role": "Configuration",
            "reason": f"Filename '{stem}' suggests configuration.",
            "importance": "medium",
            "confidence": "medium",
        }
    if any(t in stem for t in ("migration", "migrate")):
        return {
            "role": "Migration",
            "reason": f"Filename '{stem}' suggests database migration.",
            "importance": "medium",
            "confidence": "medium",
        }

    # Extension-based fallback
    if ext in SUPPORTED_EXTENSIONS:
        lang = _EXT_TO_LANG.get(ext, "Unknown")
        return {
            "role": "Application Source",
            "reason": f"{lang} source file.",
            "importance": "low",
            "confidence": "low",
        }

    return {
        "role": "Unknown",
        "reason": "Purpose not determined from static analysis.",
        "importance": "low",
        "confidence": "low",
    }


def detect_entry_points(root: str, source_files: list[str]) -> list[dict]:
    """
    Detect likely application entry points across Python, Java, and C files.

    Priority: content evidence (high) > filename convention (medium).

    Returns list of:
        { path, language, type, confidence, evidence }
    """
    entry_points: list[dict] = []
    seen_paths: set[str] = set()

    for rel in source_files:
        if rel in seen_paths:
            continue

        full = os.path.join(root, rel)
        ext = os.path.splitext(rel)[-1].lower()
        filename = os.path.basename(rel)
        rel_posix = rel.replace("\\", "/")

        try:
            with open(full, "r", encoding="utf-8", errors="replace") as f:
                source = f.read()
        except OSError:
            source = ""

        if ext == ".py":
            if _PY_MAIN_RE.search(source):
                seen_paths.add(rel)
                entry_points.append({
                    "path": rel_posix,
                    "language": "Python",
                    "type": "Application Entry Point",
                    "confidence": "high",
                    "evidence": "Contains if __name__ == '__main__'",
                })
            elif filename.lower() in _PYTHON_ENTRY_NAMES:
                seen_paths.add(rel)
                entry_points.append({
                    "path": rel_posix,
                    "language": "Python",
                    "type": "Likely Entry Point",
                    "confidence": "medium",
                    "evidence": f"Filename '{filename}' matches common Python entry-point convention",
                })

        elif ext == ".java":
            if _JAVA_MAIN_RE.search(source):
                seen_paths.add(rel)
                entry_points.append({
                    "path": rel_posix,
                    "language": "Java",
                    "type": "Application Entry Point",
                    "confidence": "high",
                    "evidence": "Contains public static void main(String[] args)",
                })

        elif ext == ".c":
            if _C_MAIN_RE.search(source):
                seen_paths.add(rel)
                entry_points.append({
                    "path": rel_posix,
                    "language": "C",
                    "type": "Application Entry Point",
                    "confidence": "high",
                    "evidence": "Contains int main(...) function definition",
                })

    # Sort: high confidence first, then alphabetically
    priority = {"high": 0, "medium": 1, "low": 2}
    entry_points.sort(key=lambda e: (priority.get(e["confidence"], 9), e["path"]))
    return entry_points


def _read_source(root: str, rel: str) -> str:
    """Safely read a source file, returning empty string on error."""
    try:
        full = os.path.join(root, rel)
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _extract_python_imports(source: str, self_path: str) -> list[str]:
    """
    Extract module names from Python import statements.
    Returns a list of raw module strings (e.g., 'os', 'services.user').
    """
    imports = []
    for m in _PY_IMPORT_RE.finditer(source):
        module = m.group(1) or m.group(2)
        if module:
            imports.append(module)
    return imports


def _resolve_python_local(
    module_name: str,
    self_path: str,
    file_set: set[str],
) -> Optional[str]:
    """
    Try to resolve a Python import string to a project-local file path.

    Attempts:
      1. module.to.path → module/to/path.py
      2. First component match for top-level packages
    """
    # Convert dotted module to path candidates
    parts = module_name.split(".")
    candidates = [
        "/".join(parts) + ".py",
        "/".join(parts) + "/__init__.py",
        parts[0] + ".py",
        parts[0] + "/__init__.py",
    ]
    for c in candidates:
        if c in file_set:
            return c
        # Also try relative to self
        self_dir = "/".join(self_path.replace("\\", "/").split("/")[:-1])
        if self_dir:
            full_candidate = self_dir + "/" + c
            if full_candidate in file_set:
                return full_candidate
    return None


def build_dependency_graph(
    root: str,
    source_files: list[str],
    entry_points: list[dict],
    max_nodes: int = 50,
) -> dict:
    """
    Build a static dependency graph from import/include statements.

    Prioritises: entry points → high fan-in → high fan-out.
    Caps graph at max_nodes to keep frontend render manageable.

    Returns:
        {
            "nodes": [ { id, label, type } ],
            "edges": [ { source, target, relationship } ],
            "local_relationships": int,
            "external_dependencies": int,
            "high_fan_in": [ path ],
            "high_fan_out": [ path ],
        }
    """
    file_set: set[str] = {f.replace("\\", "/") for f in source_files}
    posix_files = sorted(file_set)

    # { file_path → list of imported file_paths (local) }
    local_deps: dict[str, list[str]] = defaultdict(list)
    # { file_path → count of external imports }
    external_count: dict[str, int] = defaultdict(int)
    # visited for circular dep protection
    visited_read: set[str] = set()

    for rel in source_files:
        rel_posix = rel.replace("\\", "/")
        if rel_posix in visited_read:
            continue
        visited_read.add(rel_posix)

        source = _read_source(root, rel)
        ext = os.path.splitext(rel)[-1].lower()

        if ext == ".py":
            for module in _extract_python_imports(source, rel_posix):
                local = _resolve_python_local(module, rel_posix, file_set)
                if local and local != rel_posix:
                    local_deps[rel_posix].append(local)
                else:
                    external_count[rel_posix] += 1

        elif ext == ".java":
            for m in _JAVA_IMPORT_RE.finditer(source):
                imp = m.group(1)
                # Heuristic: project-local if not java.*, org.*, com.*standard patterns
                parts = imp.split(".")
                # Try to find a .java file matching the last component
                class_name = parts[-1]
                matched = False
                for f in posix_files:
                    if f.endswith(f"/{class_name}.java") or f == f"{class_name}.java":
                        if f != rel_posix:
                            local_deps[rel_posix].append(f)
                            matched = True
                            break
                if not matched:
                    external_count[rel_posix] += 1

        elif ext in (".c", ".h"):
            for m in _C_LOCAL_INCLUDE_RE.finditer(source):
                inc = m.group(1)
                # Resolve relative to file directory or root
                inc_posix = inc.replace("\\", "/")
                self_dir = "/".join(rel_posix.split("/")[:-1])
                candidates = [
                    self_dir + "/" + inc_posix if self_dir else inc_posix,
                    inc_posix,
                ]
                for c in candidates:
                    # Normalise
                    c = c.lstrip("/")
                    if c in file_set:
                        local_deps[rel_posix].append(c)
                        break
                else:
                    external_count[rel_posix] += 1

    # Compute fan-in (how many files import this file)
    fan_in: dict[str, int] = defaultdict(int)
    for deps in local_deps.values():
        for d in deps:
            fan_in[d] += 1

    # Compute fan-out
    fan_out: dict[str, int] = {f: len(deps) for f, deps in local_deps.items()}

    # Select which nodes to include (priority order)
    ep_paths = {e["path"] for e in entry_points}

    def _node_priority(path: str) -> tuple:
        return (
            0 if path in ep_paths else 1,      # entry points first
            -(fan_in.get(path, 0)),              # then high fan-in
            -(fan_out.get(path, 0)),             # then high fan-out
        )

    # Collect all files that participate in at least one local relationship
    participating = set(local_deps.keys()) | set(fan_in.keys())
    # Also include entry points even if isolated
    participating |= ep_paths & file_set

    sorted_nodes = sorted(participating, key=_node_priority)[:max_nodes]
    included_set = set(sorted_nodes)

    def _node_type(path: str) -> str:
        if path in ep_paths:
            return "entry_point"
        if fan_in.get(path, 0) >= 3:
            return "hub"
        if fan_out.get(path, 0) >= 5:
            return "high_fan_out"
        return "module"

    nodes = [
        {
            "id": p,
            "label": os.path.basename(p),
            "type": _node_type(p),
            "fan_in": fan_in.get(p, 0),
            "fan_out": fan_out.get(p, 0),
        }
        for p in sorted_nodes
    ]

    edges = []
    seen_edges: set[tuple] = set()
    for src, deps in local_deps.items():
        if src not in included_set:
            continue
        for tgt in deps:
            if tgt not in included_set:
                continue
            key = (src, tgt)
            if key in seen_edges:
                continue
            seen_edges.add(key)
            edges.append({
                "source": src,
                "target": tgt,
                "relationship": "imports",
            })

    total_local = sum(len(d) for d in local_deps.values())
    total_external = sum(external_count.values())

    high_fan_in = sorted(
        [p for p, c in fan_in.items() if c >= 2],
        key=lambda p: -fan_in[p],
    )[:10]

    high_fan_out = sorted(
        [p for p, c in fan_out.items() if c >= 3],
        key=lambda p: -fan_out[p],
    )[:10]

    return {
        "nodes": nodes,
        "edges": edges,
        "local_relationships": total_local,
        "external_dependencies": total_external,
        "high_fan_in": high_fan_in,
        "high_fan_out": high_fan_out,
    }


def identify_key_files(
    root: str,
    source_files: list[str],
    entry_points: list[dict],
    dep_graph: dict,
    top_n: int = 15,
) -> list[dict]:
    """
    Identify structurally important files for newcomers.

    Ranking criteria (additive score):
      +30  Entry point
      +20  High fan-in (many files depend on it)
      +10  High fan-out (many dependencies)
      +15  Role is Service / Controller / Repository
      +10  README or dependency manifest
       +5  Named with conventional important names

    Returns top_n files sorted by score descending.
    """
    ep_paths = {e["path"] for e in entry_points}
    high_fan_in = set(dep_graph.get("high_fan_in", []))
    high_fan_out = set(dep_graph.get("high_fan_out", []))

    # Fan-in counts from graph nodes
    fan_in_counts = {n["id"]: n.get("fan_in", 0) for n in dep_graph.get("nodes", [])}

    # Also check root-level non-source files
    root_files: list[str] = []
    try:
        for entry in os.scandir(root):
            if entry.is_file():
                root_files.append(entry.name)
    except OSError:
        pass

    scored: list[tuple[int, dict]] = []

    # Score source files
    for rel in source_files:
        rel_posix = rel.replace("\\", "/")
        filename = os.path.basename(rel)
        source = _read_source(root, rel)
        role_info = classify_file_role(rel_posix, source)

        score = 0
        reasons: list[str] = []

        if rel_posix in ep_paths:
            score += 30
            reasons.append("Application entry point")
        if rel_posix in high_fan_in:
            fi = fan_in_counts.get(rel_posix, 0)
            score += min(20, fi * 4)
            reasons.append(f"Referenced by {fi} local files")
        if rel_posix in high_fan_out:
            score += 10
            reasons.append("Has many local dependencies")
        role = role_info.get("role", "")
        if role in ("Service", "Controller", "Repository", "Domain"):
            score += 15
            reasons.append(f"Role: {role}")
        if role == "Entry Point":
            score += 20
            reasons.append("Identified as entry point")

        if score > 0:
            scored.append((score, {
                "path": rel_posix,
                "name": filename,
                "role": role,
                "reason": " · ".join(reasons) if reasons else role_info.get("reason", ""),
                "importance": "high" if score >= 30 else ("medium" if score >= 15 else "low"),
                "score": score,
            }))

    # Score root non-source files (README, requirements, etc.)
    for fname in root_files:
        fname_lower = fname.lower()
        rel_posix = fname  # root-level files
        if fname_lower in _FILE_ROLE_MAP:
            role, reason, importance = _FILE_ROLE_MAP[fname_lower]
            score = 25 if importance == "high" else (12 if importance == "medium" else 5)
            scored.append((score, {
                "path": rel_posix,
                "name": fname,
                "role": role,
                "reason": reason,
                "importance": importance,
                "score": score,
            }))

    scored.sort(key=lambda x: -x[0])
    return [item for _, item in scored[:top_n]]


def identify_important_directories(root: str) -> list[dict]:
    """
    Identify and annotate top-level directories (and one level deep for
    common source roots like src/), excluding ignored dirs.

    Returns list of { path, role, description, confidence, evidence }.
    """
    important: list[dict] = []

    def _scan_dir(dir_path: str, rel_path: str, depth: int) -> None:
        if depth > 2:
            return
        try:
            entries = list(os.scandir(dir_path))
        except OSError:
            return

        dirs = [e for e in entries if e.is_dir() and e.name not in IGNORED_DIRS and not e.name.startswith(".")]
        files = [e.name for e in entries if e.is_file()]

        for d in dirs:
            dir_rel = (rel_path + "/" + d.name).lstrip("/")
            try:
                dir_contents = [e.name for e in os.scandir(d.path)]
            except OSError:
                dir_contents = []

            role_info = classify_directory_role(d.name, dir_contents, dir_rel)
            if role_info["role"] != "Unknown":
                important.append({
                    "path": dir_rel + "/",
                    "name": d.name,
                    "role": role_info["role"],
                    "description": role_info["description"],
                    "confidence": role_info["confidence"],
                    "evidence": role_info["evidence"],
                })
            # Recurse into src/ one more level
            if d.name.lower() in ("src", "source", "app", "core", "main") and depth < 1:
                _scan_dir(d.path, dir_rel, depth + 1)

    _scan_dir(root, "", 0)

    # Deduplicate by path, keep highest confidence
    seen: dict[str, dict] = {}
    priority = {"high": 0, "medium": 1, "low": 2}
    for item in important:
        p = item["path"]
        if p not in seen or priority.get(item["confidence"], 9) < priority.get(seen[p]["confidence"], 9):
            seen[p] = item

    result = sorted(seen.values(), key=lambda x: (priority.get(x["confidence"], 9), x["path"]))
    return result[:20]  # Cap at 20 for frontend usability


def generate_reading_path(
    root: str,
    important_files: list[dict],
    important_dirs: list[dict],
    entry_points: list[dict],
) -> list[dict]:
    """
    Generate a recommended reading order for developers new to the repository.

    Ordering principle (learning progression):
      1. Documentation  (README.md)
      2. Dependency manifests
      3. Build configuration
      4. Entry points
      5. API / Controller layer
      6. Service / Business logic
      7. Repository / Persistence
      8. Data Models
      9. Utilities / Helpers
      10. Tests

    Only includes files and directories actually found in the repository.
    Does NOT reference non-existent paths.
    """
    path_steps: list[dict] = []
    added_paths: set[str] = set()

    def _add(item_path: str, reason: str) -> None:
        if item_path not in added_paths:
            added_paths.add(item_path)
            path_steps.append({"order": 0, "path": item_path, "reason": reason})

    # 1. README
    for f in important_files:
        if f["role"] == "Documentation" and "readme" in f["name"].lower():
            _add(f["path"], "Start with project purpose, setup, and usage instructions.")
            break

    # 2. Dependency manifests
    for f in important_files:
        if f["role"] == "Dependency Manifest":
            _add(f["path"], "Understand the external packages the project depends on.")

    # 3. Build configuration
    for f in important_files:
        if f["role"] == "Build Configuration" and f.get("importance") == "high":
            _add(f["path"], "Understand the build system and project structure.")

    # 4. Entry points (high confidence first)
    for ep in entry_points:
        if ep["confidence"] == "high":
            _add(ep["path"], "Understand where application execution begins.")

    # 5-10. Directories by layer priority
    role_reason: dict[str, str] = {
        "API":          "Understand public API endpoints and contracts.",
        "Controller":   "Understand request handling and routing.",
        "Service":      "Understand the core business logic.",
        "Domain":       "Understand domain concepts and rules.",
        "Repository":   "Understand data access and persistence.",
        "Data Model":   "Understand core data structures and entities.",
        "Schema":       "Understand data validation schemas.",
        "Utility":      "Understand shared utilities and helpers.",
        "Headers":      "Understand interface declarations (C headers).",
        "Tests":        "Understand expected application behaviour through tests.",
        "Configuration":"Understand application configuration options.",
    }
    layer_order = [
        "API", "Controller", "Service", "Domain",
        "Repository", "Data Model", "Schema",
        "Utility", "Headers", "Tests", "Configuration",
    ]

    for layer in layer_order:
        for d in important_dirs:
            if d["role"] == layer and d["path"] not in added_paths:
                reason = role_reason.get(layer, f"Explore {layer.lower()} components.")
                _add(d["path"], reason)
                break  # One representative per layer

    # Assign sequential order numbers
    for i, step in enumerate(path_steps, 1):
        step["order"] = i

    return path_steps


def detect_architectural_clues(important_dirs: list[dict]) -> list[dict]:
    """
    Identify possible architectural patterns from directory structure.

    Uses cautious language ("appears to", "possible", "structural indication").
    Never claims certainty without strong evidence.

    Returns list of { pattern, confidence, evidence, description }.
    """
    clues: list[dict] = []
    roles_present = {d["role"] for d in important_dirs}
    dir_names = {d["name"].lower() for d in important_dirs}

    # Layered / N-tier architecture
    layer_roles = {"Controller", "Service", "Repository", "Data Model"}
    found_layers = roles_present & layer_roles
    if len(found_layers) >= 3:
        evidence = [
            f"Directories suggesting {r} layer detected"
            for r in sorted(found_layers)
        ]
        clues.append({
            "pattern": "Possible Layered Architecture",
            "confidence": "medium" if len(found_layers) == 4 else "low",
            "evidence": evidence,
            "description": (
                "The repository appears to use a layered structure based on the presence "
                "of directories suggesting " +
                ", ".join(sorted(found_layers)) + " layers. "
                "Confirm by inspecting inter-layer dependencies."
            ),
        })

    # MVC pattern
    if "Controller" in roles_present and "Data Model" in roles_present:
        evidence = ["Controller and Model directories present"]
        if any(n in dir_names for n in ("view", "views", "template", "templates", "static", "resources")):
            evidence.append("View / template directory detected")
            clues.append({
                "pattern": "Possible MVC Structure",
                "confidence": "medium",
                "evidence": evidence,
                "description": (
                    "The repository appears to follow an MVC (Model-View-Controller) structure "
                    "based on the presence of controller, model, and view/template directories."
                ),
            })

    # Microservice / multi-service indicator
    build_files_at_depth = [d for d in important_dirs if d["role"] == "Build Configuration"]
    if len(build_files_at_depth) > 1:
        clues.append({
            "pattern": "Possible Multi-Module or Monorepo",
            "confidence": "medium",
            "evidence": [f"Multiple build configurations found: {[d['path'] for d in build_files_at_depth[:3]]}"],
            "description": (
                "Multiple build configuration files suggest this may be a multi-module "
                "project or monorepo containing independent sub-applications."
            ),
        })

    # Hexagonal / Ports-and-Adapters
    hex_names = {"port", "ports", "adapter", "adapters", "infrastructure", "application", "domain"}
    hex_found = dir_names & hex_names
    if len(hex_found) >= 3:
        clues.append({
            "pattern": "Possible Hexagonal Architecture",
            "confidence": "low",
            "evidence": [f"Directories matching hexagonal terminology: {sorted(hex_found)}"],
            "description": (
                "Directory names suggest possible hexagonal (ports-and-adapters) architecture. "
                "Verify by inspecting dependency direction."
            ),
        })

    return clues


def detect_subprojects(root: str) -> list[dict]:
    """
    Detect possible subprojects / modules in a monorepo by searching
    for independent build manifests below the top level.

    Returns list of { path, build_file, type }.
    """
    subprojects: list[dict] = []
    _SUBPROJECT_MANIFESTS = {
        "pom.xml": "Maven module",
        "build.gradle": "Gradle module",
        "build.gradle.kts": "Gradle module (Kotlin DSL)",
        "package.json": "Node.js module",
        "pyproject.toml": "Python module",
        "setup.py": "Python module",
        "cmakelists.txt": "CMake module",
    }

    for dirpath, dirnames, filenames in os.walk(root):
        # Skip root itself
        if os.path.abspath(dirpath) == os.path.abspath(root):
            continue
        dirnames[:] = [
            d for d in dirnames
            if d not in IGNORED_DIRS and not d.startswith(".")
        ]
        for fname in filenames:
            if fname.lower() in _SUBPROJECT_MANIFESTS:
                rel = os.path.relpath(dirpath, root).replace("\\", "/")
                subprojects.append({
                    "path": rel + "/",
                    "build_file": fname,
                    "type": _SUBPROJECT_MANIFESTS[fname.lower()],
                })
                break  # One manifest per directory

    return subprojects[:10]  # Cap at 10


def _build_language_breakdown(
    source_files: list[str],
    loc_data: dict,
) -> list[dict]:
    """Build the language_breakdown list from file counts and LOC data."""
    counts: dict[str, int] = {}
    for rel in source_files:
        ext = os.path.splitext(rel)[-1].lower()
        lang = _EXT_TO_LANG.get(ext)
        if lang:
            counts[lang] = counts.get(lang, 0) + 1

    total_files = sum(counts.values()) or 1
    by_lang_loc = loc_data.get("by_language", {})

    result = []
    for lang, file_count in sorted(counts.items(), key=lambda x: -x[1]):
        result.append({
            "language": lang,
            "files": file_count,
            "lines": by_lang_loc.get(lang, 0),
            "percentage": round(file_count / total_files * 100, 1),
        })
    return result


def analyze_repository_overview(
    root: str,
    repo_name: str,
    source_files: list[str],
) -> dict:
    """
    Top-level orchestrator: derive a complete structured understanding model
    from a repository's file system, source code, and project artifacts.

    This is the function called by GET /api/repository-overview.

    Args:
        root:         Absolute path to the repository root.
        repo_name:    Display name for the repository.
        source_files: Relative paths to all discovered source files.

    Returns:
        Complete repository overview dict conforming to the CUQA schema.
    """
    analysis_notes: list[str] = []

    # ── Language distribution ─────────────────────────────────────────────
    counts: dict[str, int] = {}
    for rel in source_files:
        ext = os.path.splitext(rel)[-1].lower()
        lang = _EXT_TO_LANG.get(ext)
        if lang:
            counts[lang] = counts.get(lang, 0) + 1

    sorted_langs = sorted(counts.items(), key=lambda x: -x[1])
    detected_languages = [lang for lang, _ in sorted_langs]
    primary_language = detected_languages[0] if detected_languages else None
    is_polyglot = len(detected_languages) > 1

    # ── LOC ──────────────────────────────────────────────────────────────
    loc_data = count_lines_of_code(root, source_files, cap=1000)
    if loc_data["capped"]:
        analysis_notes.append(
            "Lines of code were estimated from the first 1000 source files "
            f"(repository contains {len(source_files)} files total)."
        )

    # ── Directory count ───────────────────────────────────────────────────
    dir_count = count_directories(root)

    # ── Language breakdown ────────────────────────────────────────────────
    language_breakdown = _build_language_breakdown(source_files, loc_data)

    # ── Project artifacts (build tools, CI, etc.) ─────────────────────────
    project_artifacts = detect_project_artifacts(root)

    # ── Entry points ──────────────────────────────────────────────────────
    entry_points = detect_entry_points(root, source_files)
    if not entry_points:
        analysis_notes.append(
            "No identifiable entry point was detected. "
            "The repository may use an unconventional entry-point pattern, "
            "or the entry point may not be written in Python, Java, or C."
        )

    # ── Important directories ─────────────────────────────────────────────
    important_directories = identify_important_directories(root)

    # ── Dependency graph ──────────────────────────────────────────────────
    try:
        dep_result = build_dependency_graph(root, source_files, entry_points, max_nodes=50)
    except Exception as exc:
        dep_result = {
            "nodes": [], "edges": [],
            "local_relationships": 0, "external_dependencies": 0,
            "high_fan_in": [], "high_fan_out": [],
        }
        analysis_notes.append(f"Dependency graph analysis encountered an error: {exc}")

    # ── Key files ─────────────────────────────────────────────────────────
    important_files = identify_key_files(
        root, source_files, entry_points, dep_result, top_n=15
    )

    # ── Reading path ──────────────────────────────────────────────────────
    reading_path = generate_reading_path(
        root, important_files, important_directories, entry_points
    )

    # ── Technologies ──────────────────────────────────────────────────────
    technologies: list[dict] = []
    lang_cat = {"Python": "Language", "Java": "Language", "C": "Language"}
    for lang in detected_languages:
        technologies.append({
            "name": lang,
            "category": lang_cat.get(lang, "Language"),
            "evidence": [f"{counts.get(lang, 0)} source files"],
        })
    for cat, items in project_artifacts.items():
        for item in items:
            technologies.append({
                "name": item["name"],
                "category": cat.replace("_", " ").title(),
                "evidence": item["evidence"],
            })

    # ── Architectural clues ───────────────────────────────────────────────
    architectural_clues = detect_architectural_clues(important_directories)

    # ── Subprojects ───────────────────────────────────────────────────────
    subprojects = detect_subprojects(root)
    if subprojects:
        analysis_notes.append(
            f"{len(subprojects)} possible subproject(s) detected. "
            "This may be a monorepo or multi-module project."
        )

    # ── Dependency summary ────────────────────────────────────────────────
    dependency_summary = {
        "local_relationships": dep_result["local_relationships"],
        "external_dependencies": dep_result["external_dependencies"],
        "high_fan_in": dep_result["high_fan_in"],
        "high_fan_out": dep_result["high_fan_out"],
    }

    return {
        "repository": {
            "name": repo_name,
            "source_files": len(source_files),
            "directories": dir_count,
            "lines_of_code": loc_data["total"],
            "primary_language": primary_language,
            "detected_languages": detected_languages,
            "is_polyglot": is_polyglot,
        },
        "language_breakdown": language_breakdown,
        "project_artifacts": project_artifacts,
        "entry_points": entry_points,
        "important_directories": important_directories,
        "important_files": important_files,
        "recommended_reading_path": reading_path,
        "technologies": technologies,
        "dependency_graph": {
            "nodes": dep_result["nodes"],
            "edges": dep_result["edges"],
        },
        "dependency_summary": dependency_summary,
        "architectural_clues": architectural_clues,
        "subprojects": subprojects,
        "analysis_notes": analysis_notes,
    }
