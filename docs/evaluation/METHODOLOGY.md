# Empirical Evaluation Methodology for Code Smell Detection

## 1. Evaluation Objective

The goal of this evaluation framework is to empirically quantify the detection performance of CUQA (Code Understanding & Quality Assessment) against an independent human-annotated ground-truth benchmark across multiple programming languages (**Python**, **Java**, and **C**).

The research question answered is:
> *"How effectively does CUQA identify code smells and language-specific quality issues compared with an independently labelled ground-truth oracle?"*

---

## 2. Distinction: Software Verification vs Empirical Evaluation

- **Software Verification (Unit Testing)**:
  - Validates that CUQA's code rules execute strictly according to specification (e.g. `LongMethod` triggers when code lines > 30).
  - Executed via `pytest` in `agents/cuqa_agent/tests/`.
  - **Does NOT** represent detection accuracy against real-world code quality issues.

- **Empirical Evaluation (Ground-Truth Benchmarking)**:
  - Compares CUQA predictions against independent human annotations ($y_i \in \{0, 1\}$).
  - Measures true positives ($TP$), false positives ($FP$), false negatives ($FN$), and true negatives ($TN$).
  - Produces statistically rigorous metrics: **Precision**, **Recall**, **F1-Score**, **MCC**, **Macro F1**, **Micro F1**, and **Bootstrap 95% Confidence Intervals**.

---

## 3. Unit of Analysis & Entity Level Matching

Each ground-truth sample specifies an explicit entity granularity:
- **Method / Function**: `LongMethod`, `LongFunction`, `TooManyParameters`, `DeepNesting`, `SwitchStatements`, `MessageChains`, `UnreachableCode`, `UnusedVariable`, `FeatureEnvy`.
- **Class**: `LargeClass`, `LazyClass`, `PrimitiveObsession`, `InappropriateIntimacy`, `SpeculativeGenerality`, `DataClass`.
- **Declaration / Occurrence**: `GlobalVariable` (declaration scope), `UnsafeFunctionUsage` (function call name), `MagicNumber` (literal token).
- **File**: `LargeHeaderFile`, `Comments`.

Matching between CUQA predictions and Ground-Truth records requires matching:
$$\text{MatchKey} = (\text{Language}, \text{RelativeFilePath}, \text{SmellType}, \text{EntityName})$$
with optional line range overlap checking ($\pm 5$ lines tolerance for boundary line differences across parsers).

---

## 4. Confusion Matrix Definitions

- **True Positive ($TP$)**: CUQA predicts smell exists ($1$) AND Ground Truth consensus label is $1$.
- **False Positive ($FP$)**: CUQA predicts smell exists ($1$) BUT Ground Truth consensus label is $0$.
- **False Negative ($FN$)**: Ground Truth consensus label is $1$ BUT CUQA failed to detect the smell ($0$).
- **True Negative ($TN$)**: Ground Truth consensus label is $0$ AND CUQA correctly emitted no smell prediction ($0$).

---

## 5. Statistical Metrics & Mathematical Definitions

1. **Precision**:
   $$\text{Precision} = \frac{TP}{TP + FP}$$
2. **Recall / Sensitivity**:
   $$\text{Recall} = \frac{TP}{TP + FN}$$
3. **F1-Score**:
   $$\text{F1} = \frac{2 \times \text{Precision} \times \text{Recall}}{\text{Precision} + \text{Recall}}$$
4. **Accuracy**:
   $$\text{Accuracy} = \frac{TP + TN}{TP + TN + FP + FN}$$
5. **Specificity**:
   $$\text{Specificity} = \frac{TN}{TN + FP}$$
6. **False Positive Rate ($FPR$)**:
   $$\text{FPR} = \frac{FP}{FP + TN}$$
7. **False Negative Rate ($FNR$)**:
   $$\text{FNR} = \frac{FN}{FN + TP}$$
8. **Matthews Correlation Coefficient ($MCC$)**:
   $$\text{MCC} = \frac{TP \times TN - FP \times FN}{\sqrt{(TP + FP)(TP + FN)(TN + FP)(TN + FN)}}$$
   *(Note: If any denominator term equals $0$, metric returns `None` with zero-division explanation).*

---

## 6. Aggregation Strategies: Macro vs Micro

- **Per-Language Macro F1**:
  $$\text{Macro F1}_{\text{Lang}} = \frac{1}{|S_{\text{Lang}}|} \sum_{s \in S_{\text{Lang}}} \text{F1}_s$$
  where $S_{\text{Lang}}$ is the set of evaluated smell types in that language with valid F1 scores.

- **Micro F1**:
  Calculated by pooling total $TP$, $FP$, and $FN$ across all smells:
  $$\text{Micro Precision} = \frac{\sum TP}{\sum TP + \sum FP}, \quad \text{Micro Recall} = \frac{\sum TP}{\sum TP + \sum FN}$$
  $$\text{Micro F1} = \frac{2 \times \text{Micro Precision} \times \text{Micro Recall}}{\text{Micro Precision} + \text{Micro Recall}}$$

- **Overall Macro F1**:
  The unweighted arithmetic mean of individual smell F1 scores across the entire benchmark suite.

---

## 7. Inter-Rater Agreement (Cohen's Kappa)

When multiple human reviewers label the ground-truth samples ($r_1, r_2 \in \{0, 1\}$), inter-rater reliability is measured using **Cohen's Kappa ($\kappa$)**:

$$\kappa = \frac{P_o - P_e}{1 - P_e}$$
where:
- $P_o$ is the observed proportional agreement:
  $$P_o = \frac{a + d}{N}$$
- $P_e$ is the expected agreement by chance:
  $$P_e = \left(\frac{a+b}{N} \times \frac{a+c}{N}\right) + \left(\frac{c+d}{N} \times \frac{b+d}{N}\right)$$

---

## 8. Non-Circular Ground Truth Guidelines

- Ground-truth labels **must never** be generated using CUQA's output or internal threshold rules. Doing so introduces severe circular reasoning bias and invalidates scientific evaluation.
- All evaluation results must state whether they originate from:
  1. **Experiment A (Controlled Benchmark)**: Verification on synthetic edge cases.
  2. **Experiment B (Real-World Ground Truth)**: Empirical evaluation on human-annotated repositories.
