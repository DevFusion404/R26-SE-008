"""
CodeBERT ML Scoring Module
============================

Uses a pre-trained CodeBERT model (``microsoft/codebert-base``) to produce
embedding-based scores for refactoring candidates.  This module adds an
ML-driven signal to the existing heuristic scoring pipeline.

The scorer encodes two pieces of text through CodeBERT:
    1. A **smell description** built from the :class:`CodeSmell` object.
    2. A **candidate description** built from the refactoring candidate dict.

From the resulting 768-dimensional embeddings it derives:
    - **Contextual suitability** — cosine similarity (rescaled to 0–1).
    - **Quality improvement** — projection onto a pre-computed quality axis.
    - **Behavioral risk** — distance from known high-risk patterns.
    - **Confidence** — based on embedding norms and similarity spread.

The model is **lazy-loaded** (only when ``predict`` is first called) to
avoid the ~500 MB download cost on import.  If ``transformers`` or ``torch``
are not installed the module returns neutral default scores so the rest of
the pipeline continues to work.

Usage::

    from rdp_agent.ml_scorer import MLScorer

    scorer = MLScorer()
    if scorer.is_available():
        prediction = scorer.predict(smell, candidate)
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional

from .models import CodeSmell, MLPrediction

logger = logging.getLogger("rdp_agent.ml_scorer")


# ---------------------------------------------------------------------------
# Reference texts used to build quality / risk direction vectors
# ---------------------------------------------------------------------------

# These short descriptions anchor the embedding space so we can project
# candidate embeddings onto meaningful axes.  They are intentionally
# simple — the cosine direction between them captures the semantic
# gradient "low quality → high quality" and "low risk → high risk".

_HIGH_QUALITY_REF = (
    "Excellent refactoring that significantly improves code readability, "
    "reduces complexity, increases cohesion, and follows SOLID principles. "
    "Clean code with low coupling and high maintainability."
)
_LOW_QUALITY_REF = (
    "Poor refactoring that increases complexity, introduces code duplication, "
    "violates single responsibility, and makes maintenance harder. "
    "Tangled dependencies with low cohesion."
)

_HIGH_RISK_REF = (
    "Dangerous refactoring with high chance of introducing regressions, "
    "breaking existing tests, causing runtime errors, and changing "
    "observable behavior. Complex transformation with many side effects."
)
_LOW_RISK_REF = (
    "Safe refactoring with minimal chance of introducing regressions. "
    "Preserves all existing behavior, simple mechanical transformation, "
    "well supported by automated tools."
)


# ---------------------------------------------------------------------------
# MLScorer
# ---------------------------------------------------------------------------


class MLScorer:
    """CodeBERT-based ML scoring for refactoring candidates.

    The scorer is designed to be used as a drop-in component in the
    RDP Agent pipeline.  It lazy-loads the model on first use and
    caches reference embeddings for the quality and risk axes.

    Args:
        model_name: HuggingFace model identifier.  Defaults to
                    ``"microsoft/codebert-base"``.
    """

    def __init__(self, model_name: str = "microsoft/codebert-base") -> None:
        self._model_name = model_name
        self._tokenizer = None
        self._model = None
        self._is_available: Optional[bool] = None

        # Cached reference embeddings (computed on first predict call)
        self._quality_high_emb = None
        self._quality_low_emb = None
        self._risk_high_emb = None
        self._risk_low_emb = None
        self._refs_ready = False

    # ----- Public helpers -----

    def is_available(self) -> bool:
        """Return ``True`` if ``transformers`` and ``torch`` are importable."""
        if self._is_available is None:
            try:
                import transformers  # noqa: F401
                import torch  # noqa: F401

                self._is_available = True
            except ImportError:
                self._is_available = False
        return self._is_available

    # ----- Lazy model loading -----

    def _load_model(self) -> None:
        """Load the CodeBERT tokenizer and model (downloads on first use)."""
        if self._tokenizer is not None:
            return  # already loaded

        if not self.is_available():
            raise RuntimeError(
                "Cannot load ML model: 'transformers' and/or 'torch' "
                "are not installed."
            )

        from transformers import AutoTokenizer, AutoModel  # type: ignore

        logger.info("Loading CodeBERT model '%s' …", self._model_name)
        self._tokenizer = AutoTokenizer.from_pretrained(
            self._model_name, 
            use_safetensors=False, 
            local_files_only=True
        )
        self._model = AutoModel.from_pretrained(
            self._model_name, 
            use_safetensors=False, 
            local_files_only=True
        )
        self._model.eval()  # inference mode
        logger.info("CodeBERT model loaded successfully.")

    def _ensure_references(self) -> None:
        """Compute and cache reference embeddings for quality/risk axes."""
        if self._refs_ready:
            return

        self._quality_high_emb = self._encode(_HIGH_QUALITY_REF)
        self._quality_low_emb = self._encode(_LOW_QUALITY_REF)
        self._risk_high_emb = self._encode(_HIGH_RISK_REF)
        self._risk_low_emb = self._encode(_LOW_RISK_REF)
        self._refs_ready = True
        logger.debug("Reference embeddings cached.")

    # ----- Encoding -----

    def _encode(self, text: str):
        """Encode *text* into a 768-dim CodeBERT embedding tensor.

        Uses the ``[CLS]`` token representation from the last hidden state.

        Args:
            text: Input text (will be truncated to 512 tokens).

        Returns:
            ``torch.Tensor`` of shape ``(768,)``.
        """
        import torch  # type: ignore

        self._load_model()

        inputs = self._tokenizer(
            text,
            return_tensors="pt",
            truncation=True,
            max_length=512,
            padding=True,
        )

        with torch.no_grad():
            outputs = self._model(**inputs)

        # [CLS] token is always index 0
        cls_embedding = outputs.last_hidden_state[:, 0, :].squeeze(0)
        return cls_embedding

    # ----- Text builders -----

    @staticmethod
    def _build_smell_text(smell: CodeSmell) -> str:
        """Build a natural-language description from a :class:`CodeSmell`.

        The description includes type, severity, location, and metrics so
        that CodeBERT can capture the full context.
        """
        parts = [f"Code smell: {smell.type}."]
        parts.append(f"Severity: {smell.severity}.")

        # Location
        cls = smell.location.get("class", "")
        method = smell.location.get("method", "")
        if cls:
            parts.append(f"Class: {cls}.")
        if method:
            parts.append(f"Method: {method}.")

        # Metrics
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
        """Build a natural-language description from a candidate dict."""
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

    # ----- Scoring helpers -----

    @staticmethod
    def _cosine_similarity(a, b) -> float:
        """Compute cosine similarity between two tensors."""
        import torch  # type: ignore

        a_norm = torch.nn.functional.normalize(a.unsqueeze(0), dim=1)
        b_norm = torch.nn.functional.normalize(b.unsqueeze(0), dim=1)
        sim = torch.mm(a_norm, b_norm.t()).item()
        return sim

    def _project_onto_axis(self, embedding, high_ref, low_ref) -> float:
        """Project *embedding* onto the axis defined by *high_ref - low_ref*.

        Returns a value in [0, 1] where 1 means the embedding is closest
        to the high reference and 0 means closest to the low reference.
        """
        sim_high = self._cosine_similarity(embedding, high_ref)
        sim_low = self._cosine_similarity(embedding, low_ref)

        # Rescale so that equal similarity → 0.5
        diff = sim_high - sim_low
        # diff is typically in [-0.3, +0.3]; rescale to [0, 1]
        score = 0.5 + diff * 1.5
        return max(0.0, min(1.0, score))

    # ----- Core prediction -----

    def predict(
        self,
        smell: CodeSmell,
        candidate: Dict[str, Any],
    ) -> MLPrediction:
        """Produce ML-based scores for a smell–candidate pair.

        If the model is not available (missing dependencies), returns
        a neutral ``MLPrediction`` with all scores at 0.5 and
        confidence = 0.

        Args:
            smell: The code smell being addressed.
            candidate: Candidate refactoring dictionary.

        Returns:
            An :class:`MLPrediction` instance.
        """
        refactoring_name = candidate.get("name", "Unknown")

        if not self.is_available():
            logger.debug(
                "ML scoring unavailable; returning neutral prediction "
                "for '%s'.",
                refactoring_name,
            )
            return MLPrediction(
                refactoring=refactoring_name,
                smell_id=smell.id,
                contextual_suitability=0.5,
                quality_improvement=0.5,
                behavioral_risk=0.5,
                confidence=0.0,
                embedding_norm=0.0,
            )

        import torch  # type: ignore

        try:
            self._load_model()
            self._ensure_references()

            # Encode smell context and candidate description
            smell_text = self._build_smell_text(smell)
            candidate_text = self._build_candidate_text(candidate)

            smell_emb = self._encode(smell_text)
            candidate_emb = self._encode(candidate_text)

            # Combined embedding (element-wise mean) for axis projections
            combined_emb = (smell_emb + candidate_emb) / 2.0

            # --- Contextual suitability ---
            # Cosine similarity between smell and candidate embeddings,
            # rescaled from [-1, 1] to [0, 1].
            raw_sim = self._cosine_similarity(smell_emb, candidate_emb)
            contextual_suitability = (raw_sim + 1.0) / 2.0
            contextual_suitability = max(0.0, min(1.0, contextual_suitability))

            # --- Quality improvement ---
            quality_improvement = self._project_onto_axis(
                combined_emb,
                self._quality_high_emb,
                self._quality_low_emb,
            )

            # --- Behavioral risk ---
            behavioral_risk = self._project_onto_axis(
                combined_emb,
                self._risk_high_emb,
                self._risk_low_emb,
            )

            # --- Confidence ---
            # Based on embedding norm (higher norm → model is more
            # "opinionated") and similarity spread.
            emb_norm = torch.norm(combined_emb).item()
            # Normalise: typical CLS norms are 5–15 for CodeBERT
            norm_confidence = min(1.0, emb_norm / 12.0)

            # Similarity spread: how different are the quality and risk
            # projections from 0.5 (neutral)?  Higher spread → more
            # confident the model has a clear signal.
            spread = (
                abs(quality_improvement - 0.5) + abs(behavioral_risk - 0.5)
            ) / 1.0
            spread_confidence = min(1.0, spread)

            confidence = round(
                0.6 * norm_confidence + 0.4 * spread_confidence, 3
            )

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
                refactoring_name,
                smell.id,
                contextual_suitability,
                quality_improvement,
                behavioral_risk,
                confidence,
            )

            return prediction

        except Exception as exc:
            logger.warning(
                "ML scoring failed for '%s' on smell %s: %s. "
                "Returning neutral prediction.",
                refactoring_name,
                smell.id,
                exc,
            )
            return MLPrediction(
                refactoring=refactoring_name,
                smell_id=smell.id,
                contextual_suitability=0.5,
                quality_improvement=0.5,
                behavioral_risk=0.5,
                confidence=0.0,
                embedding_norm=0.0,
            )

    # ----- Batch prediction -----

    def predict_all(
        self,
        smell: CodeSmell,
        candidates: List[Dict[str, Any]],
    ) -> List[MLPrediction]:
        """Predict ML scores for every candidate in the list.

        Args:
            smell: The code smell being addressed.
            candidates: List of candidate refactoring dictionaries.

        Returns:
            List of :class:`MLPrediction` instances (same order as input).
        """
        return [self.predict(smell, c) for c in candidates]
