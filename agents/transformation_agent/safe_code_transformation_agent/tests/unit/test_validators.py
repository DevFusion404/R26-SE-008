from sctva.validators.syntax_validator import SyntaxValidator
from sctva.validators.structural_validator import StructuralValidator


def test_python_syntax_validator_passes_valid_code():
    validator = SyntaxValidator()
    result = validator.validate(
        language="python",
        source_code="def a():\n    return 1\n",
        require_compilation=False,
        timeout_seconds=5,
    )
    assert result.passed is True


def test_python_syntax_validator_fails_invalid_code():
    validator = SyntaxValidator()
    result = validator.validate(
        language="python",
        source_code="def a(:\n    return 1\n",
        require_compilation=False,
        timeout_seconds=5,
    )
    assert result.passed is False


def test_structural_similarity_python_high_for_small_change():
    validator = StructuralValidator()
    result = validator.validate(
        language="python",
        original_code="def add(a,b):\n    return a+b\n",
        transformed_code="def add(a, b):\n    return a + b\n",
    )
    assert result.score >= 0.8


def test_c_syntax_validator_passes_valid_code_without_compiler(monkeypatch):
    monkeypatch.setattr("sctva.validators.syntax_validator.shutil.which", lambda name: None)
    validator = SyntaxValidator()
    result = validator.validate(
        language="c",
        source_code="int add(int a, int b) { return a + b; }\n",
        require_compilation=True,
        timeout_seconds=5,
    )
    assert result.passed is True


def test_c_syntax_validator_fails_invalid_code():
    validator = SyntaxValidator()
    result = validator.validate(
        language="c",
        source_code="int add(int a, int b) { return a + b\n",
        require_compilation=False,
        timeout_seconds=5,
    )
    assert result.passed is False


def test_c_structural_validator_high_for_small_change():
    validator = StructuralValidator()
    result = validator.validate(
        language="c",
        original_code="int add(int a, int b) { return a + b; }\n",
        transformed_code="int add(int a, int b) { return a + b; }\n",
    )
    assert result.score >= 0.8
