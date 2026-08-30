# PP2 Presentation Results Guide

This guide specifies how to report CUQA evaluation results accurately in your Project Presentation 2 (PP2) slides and academic reports.

---

## 1. Safe Metrics for Presentation Slides

When presenting CUQA's code smell detection performance, use these primary metrics:

1. **Per-Language Macro F1**:
   - Python Macro F1
   - Java Macro F1
   - C Macro F1
2. **Overall Macro F1**:
   - Arithmetic mean of F1 scores across evaluated smells.
3. **Precision and Recall**:
   - Reported alongside F1 to explain trade-offs (e.g. high precision vs conservative recall).
4. **Sample Size ($N$)**:
   - Total number of evaluated code entities in the ground-truth dataset.
5. **Inter-Rater Reliability ($\kappa$)**:
   - Cohen's Kappa score for human ground-truth annotation consensus.

---

## 2. Terminology Rules for Presentations

| Scientific Term | Correct Usage | INCORRECT / Forbidden Usage |
|---|---|---|
| **Macro F1** | "CUQA achieved an overall Macro F1 of X.XX on real-world ground truth." | "CUQA achieved X% Accuracy." |
| **Precision** | "CUQA's smell detection precision is X.XX." | "Rule pass rate." |
| **Unit Test Pass Rate** | "100% of rule verification unit tests passed in pytest." | "100% smell detection accuracy." |
| **Controlled Benchmark** | "Controlled rule-boundary test." | "Real-world empirical performance." |

---

## 3. Recommended Slide Table Structure

```
+-------------------------------------------------------------------+
|  Language   |  Precision  |   Recall   |  Macro F1  | Sample Size |
+-------------+-------------+------------+------------+-------------+
|  Python     |    0.XX     |    0.XX    |    0.XX    |    N = XX   |
|  Java       |    0.XX     |    0.XX    |    0.XX    |    N = XX   |
|  C          |    0.XX     |    0.XX    |    0.XX    |    N = XX   |
+-------------+-------------+------------+------------+-------------+
|  Overall    |    0.XX     |    0.XX    |    0.XX    |    N = XX   |
+-------------------------------------------------------------------+
  Inter-rater Agreement (Cohen's Kappa): \kappa = 0.XX (N_annotated = XX)
```

---

## 4. Reporting "Not Yet Evaluated" State

If human ground-truth annotations are pending:
- State clearly: **"Evaluation Framework Infrastructure Complete; Empirical Benchmarking Pending Human Annotation."**
- Show the architecture diagram and template schema.
- **Do NOT** report fabricated scores or default 100% values.
