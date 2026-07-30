"""
CodeBERT ML Scoring Module
===========================
Scores refactoring candidates using CodeBERT embeddings. Encodes a smell
description and a candidate description into 768-dim vectors, then derives
contextual suitability, quality improvement, and behavioral risk scores by
projecting against pre-computed quality/risk reference axes.

Model is lazy-loaded on first use. Falls back to neutral scores (0.5) if
transformers/torch are not installed.
"""
from __future__ import annotations
import logging
from typing import Any, Dict, List, Optional
from .models import CodeSmell, MLPrediction
logger = logging.getLogger("rdp_agent.ml_scorer")

# ---------------------------------------------------------------------------
# Reference anchor texts — define the quality and risk axes in embedding space.
# Concrete metrics and examples are intentional so CodeBERT projects
# meaningfully onto these dimensions.
# ---------------------------------------------------------------------------

_HIGH_QUALITY_REF = (
    "Extract Method refactoring that breaks a long 150-line function with "
    "cyclomatic complexity 18 into smaller, focused methods with complexity 3-5 each. "
    "Reduces LOC per method from 150 to 30-50, improves testability, single responsibility. "
    "Extract Class for 25-method God Class into 2-3 cohesive classes with 8-10 methods each. "
    "Eliminate duplicate code blocks via Extract Method. Reduce parameter count from 8+ to 3-4. "
    "Replace long conditional chains with polymorphism. Introduce Parameter Object for data clumps. "
    "Move Method from Feature Envy class to proper owner. Hide Delegate to reduce coupling from 12 to 5. "
    "Result: cyclomatic complexity reduced 40%, maintainability score +30%, coupling -50%."
)

_LOW_QUALITY_REF = (
    "Inline code with high duplication, creating 500+ line megamethods. "
    "Merge classes causing 40+ method God Class with cyclomatic complexity 25+. "
    "Add more parameters to methods already having 10+ parameters. "
    "Deep nesting (5+ levels) and hard-coded magic numbers (99, -1, 256) throughout. "
    "Circular dependencies between 15+ classes. No abstraction layers. "
    "Copy-paste code in 20+ locations. Single class doing validation, persistence, and business logic. "
    "Result: code duplication 60%, coupling 20+, cyclomatic complexity 30+, maintainability score -40%."
)

_HIGH_RISK_REF = (
    "Inline a class used in 50+ places without updating all call sites. "
    "Extract Subclass with complex inheritance chains affecting 8 classes. "
    "Move Method from class A to B when A has 200+ dependencies. "
    "Replace Conditional with Polymorphism in high-frequency code path (10k+ calls/sec). "
    "Collapse Hierarchy with non-trivial method overrides in 12+ child classes. "
    "Refactoring touches core data model, persistence layer, and API contract. "
    "No comprehensive test coverage (< 30%). High coupling (15+). Complex side effects."
)

_LOW_RISK_REF = (
    "Rename Method with no external callers (private, 0 usages outside class). "
    "Extract Method from single-caller function with clear boundaries. "
    "Remove Dead Code that is never executed (dead branch, unreachable method). "
    "Introduce Constant by replacing magic number 99 in a single location. "
    "Pull Up Method into interface shared by 2 classes with identical implementation. "
    "Hide Delegate with 1:1 forwarding method, no semantic change. "
    "Refactoring is local (< 5 methods affected), high test coverage (> 80%), low coupling (< 3)."
)

class MLScorer:
    """CodeBERT-based ML scorer for refactoring candidates.
    Lazy-loads microsoft/codebert-base on first predict() call and caches
    reference embeddings for the quality and risk projection axes.
    Args:
        model_name: HuggingFace model identifier. Defaults to "microsoft/codebert-base".
    """
    def __init__(self, model_name: str = "microsoft/codebert-base") -> None:
        self._model_name = model_name
        self._tokenizer = None
        self._model = None
        self._is_available: Optional[bool] = None
        # Reference embeddings — computed once on first predict call
        self._quality_high_emb = None
        self._quality_low_emb = None
        self._risk_high_emb = None
        self._risk_low_emb = None
        self._refs_ready = False

    def is_available(self) -> bool:
        """Return True if transformers and torch are importable."""
        if self._is_available is None:
            try:
                import transformers  # noqa: F401
                import torch  # noqa: F401
                self._is_available = True
            except ImportError:
                self._is_available = False
        return self._is_available

    def _load_model(self) -> None:
        """Load CodeBERT tokenizer and model. Tries local cache first, then downloads."""
        if self._tokenizer is not None:
            return

        if not self.is_available():
            raise RuntimeError(
                "Cannot load ML model: 'transformers' and/or 'torch' are not installed."
            )
        from transformers import AutoTokenizer, AutoModel  # type: ignore
        logger.info("Loading CodeBERT model '%s' …", self._model_name)
        try:
            # Prefer local cache to keep startup fast and offline-friendly
            self._tokenizer = AutoTokenizer.from_pretrained(
                self._model_name, use_safetensors=False, local_files_only=True
            )
            self._model = AutoModel.from_pretrained(
                self._model_name, use_safetensors=False, local_files_only=True
            )
        except Exception:
            logger.info("Local cache not found; downloading '%s'.", self._model_name)
            self._tokenizer = AutoTokenizer.from_pretrained(
                self._model_name, use_safetensors=False, local_files_only=False
            )
            self._model = AutoModel.from_pretrained(
                self._model_name, use_safetensors=False, local_files_only=False
            )

        self._model.eval()
        logger.info("CodeBERT model loaded successfully.")

    def _ensure_references(self) -> None:
        """Encode and cache the four quality/risk anchor texts (runs once)."""
        if self._refs_ready:
            return
        self._quality_high_emb = self._encode(_HIGH_QUALITY_REF)
        self._quality_low_emb = self._encode(_LOW_QUALITY_REF)
        self._risk_high_emb = self._encode(_HIGH_RISK_REF)
        self._risk_low_emb = self._encode(_LOW_RISK_REF)
        self._refs_ready = True
        logger.debug("Reference embeddings cached.")

    def _encode(self, text: str):
        """Encode text into a 768-dim CLS token embedding via CodeBERT.

        Args:
            text: Input string (truncated to 512 tokens).

        Returns:
            torch.Tensor of shape (768,).
        """
        import torch  # type: ignore

        self._load_model()
        inputs = self._tokenizer(
            text, return_tensors="pt", truncation=True, max_length=512, padding=True
        )
        with torch.no_grad():
            outputs = self._model(**inputs)
        # CLS token (index 0) captures sentence-level semantics
        return outputs.last_hidden_state[:, 0, :].squeeze(0)

    @staticmethod
    def _build_smell_text(smell: CodeSmell) -> str:
        """Convert a CodeSmell into a natural-language sentence for CodeBERT."""
        parts = [f"Code smell: {smell.type}.", f"Severity: {smell.severity}."]

        cls = smell.location.get("class", "")
        method = smell.location.get("method", "")
        if cls:
            parts.append(f"Class: {cls}.")
        if method:
            parts.append(f"Method: {method}.")

        loc = smell.metrics.get("lines_of_code")
        cc = smell.metrics.get("cyclomatic_complexity")
        mc = smell.metrics.get("method_count")
        if loc:
            parts.append(f"Lines of code: {loc}.")
        if cc:
            parts.append(f"Cyclomatic complexity: {cc}.")
        if mc:
            parts.append(f"Method count: {mc}.")
        if smell.details:
            parts.append(f"Details: {smell.details}")

        return " ".join(parts)

    @staticmethod
    def _build_candidate_text(candidate: Dict[str, Any]) -> str:
        """Convert a candidate dict into a natural-language sentence for CodeBERT."""
        name = candidate.get("name", "Unknown Refactoring")
        complexity = candidate.get("complexity", "medium")
        risk = candidate.get("risk", "medium")
        impact = candidate.get("impact", "medium")
        return (
            f"Refactoring technique: {name}. "
            f"Implementation complexity: {complexity}. "
            f"Risk level: {risk}. "
            f"Expected impact: {impact}."
        )

    @staticmethod
    def _cosine_similarity(a, b) -> float:
        """Cosine similarity between two tensors, returns float in [-1, 1]."""
        import torch  # type: ignore
        a_norm = torch.nn.functional.normalize(a.unsqueeze(0), dim=1)
        b_norm = torch.nn.functional.normalize(b.unsqueeze(0), dim=1)
        return torch.mm(a_norm, b_norm.t()).item()

    def _project_onto_axis(self, embedding, high_ref, low_ref) -> float:
        """Project embedding onto the axis between high_ref and low_ref.

        Returns a value in [0, 1]: 1 = closest to high_ref, 0 = closest to low_ref.
        Difference is typically in [-0.3, +0.3], rescaled to [0, 1] via ×1.5 shift.
        """
        diff = self._cosine_similarity(embedding, high_ref) - self._cosine_similarity(embedding, low_ref)
        return max(0.0, min(1.0, 0.5 + diff * 1.5))

    def predict(self, smell: CodeSmell, candidate: Dict[str, Any]) -> MLPrediction:
        """Score a smell–candidate pair using CodeBERT embeddings.

        Returns neutral scores (all 0.5, confidence 0) if ML is unavailable.

        Args:
            smell: The detected code smell.
            candidate: Refactoring candidate dictionary.

        Returns:
            MLPrediction with suitability, quality, risk, and confidence scores.
        """
        refactoring_name = candidate.get("name", "Unknown")

        if not self.is_available():
            logger.debug("ML unavailable; returning neutral prediction for '%s'.", refactoring_name)
            return self._neutral_prediction(refactoring_name, smell.id)

        import torch  # type: ignore

        try:
            self._load_model()
            self._ensure_references()

            smell_emb = self._encode(self._build_smell_text(smell))
            candidate_emb = self._encode(self._build_candidate_text(candidate))
            combined_emb = (smell_emb + candidate_emb) / 2.0  # element-wise mean

            # Cosine similarity between smell and candidate — rescaled to [0, 1]
            raw_sim = self._cosine_similarity(smell_emb, candidate_emb)
            contextual_suitability = max(0.0, min(1.0, (raw_sim + 1.0) / 2.0))

            # Project combined embedding onto quality and risk axes
            quality_improvement = self._project_onto_axis(
                combined_emb, self._quality_high_emb, self._quality_low_emb
            )
            behavioral_risk = self._project_onto_axis(
                combined_emb, self._risk_high_emb, self._risk_low_emb
            )

            # Confidence: weighted blend of embedding norm and axis spread
            # Typical CLS norms for CodeBERT are 5–15; normalise against 12
            emb_norm = torch.norm(combined_emb).item()
            norm_confidence = min(1.0, emb_norm / 12.0)
            spread = abs(quality_improvement - 0.5) + abs(behavioral_risk - 0.5)
            confidence = round(0.6 * norm_confidence + 0.4 * min(1.0, spread), 3)

            prediction = MLPrediction(
                refactoring=refactoring_name,
                smell_id=smell.id,
                contextual_suitability=round(contextual_suitability, 4),
                quality_improvement=round(quality_improvement, 4),
                behavioral_risk=round(behavioral_risk, 4),
                confidence=confidence,
                embedding_norm=round(emb_norm, 4),
            )

            logger.debug(
                "ML prediction for '%s' on smell %s: "
                "suitability=%.3f quality=%.3f risk=%.3f confidence=%.3f",
                refactoring_name, smell.id,
                contextual_suitability, quality_improvement, behavioral_risk, confidence,
            )
            return prediction

        except Exception as exc:
            logger.warning(
                "ML scoring failed for '%s' on smell %s: %s. Returning neutral prediction.",
                refactoring_name, smell.id, exc,
            )
            return self._neutral_prediction(refactoring_name, smell.id)

    def predict_all(self, smell: CodeSmell, candidates: List[Dict[str, Any]]) -> List[MLPrediction]:
        """Run predict() for every candidate. Returns results in the same order."""
        return [self.predict(smell, c) for c in candidates]

    @staticmethod
    def _neutral_prediction(refactoring: str, smell_id: str) -> MLPrediction:
        """Return a neutral MLPrediction (all scores 0.5, confidence 0)."""
        return MLPrediction(
            refactoring=refactoring,
            smell_id=smell_id,
            contextual_suitability=0.5,
            quality_improvement=0.5,
            behavioral_risk=0.5,
            confidence=0.0,
            embedding_norm=0.0,
        )
