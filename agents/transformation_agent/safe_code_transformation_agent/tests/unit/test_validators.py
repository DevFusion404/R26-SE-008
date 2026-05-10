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
