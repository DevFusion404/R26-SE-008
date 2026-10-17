# CUQA ML Smell Model: Method, Results and Discussion

This document describes the machine-learning code smell detector that was added
to the CUQA agent. It explains which data was used, how the model was trained,
how it was evaluated and what the results mean.

All numbers come from `training/results/metrics.json`, which is written by
`training/train_model.py`, or from the other scripts in `training/`. No number
in this document is an estimate.

## 1. Summary

The model detects code smells in Java classes and methods from measurements of
the code. It does not use hand-set thresholds. The thresholds are learned from
4,277 code samples that were labeled by human reviewers.

Results on 100 repositories that were not used for training:

| Smell | Precision | Recall | F1 | MCC | ROC-AUC | PR-AUC | In shipped model |
|---|---:|---:|---:|---:|---:|---:|---|
| Long Method | 0.829 | 0.659 | 0.734 | 0.707 | 0.982 | 0.902 | yes |
| Data Class | 0.702 | 0.714 | 0.708 | 0.657 | 0.941 | 0.770 | yes |
| Blob (Large Class) | 0.458 | 0.614 | 0.524 | 0.458 | 0.904 | 0.574 | yes |
| Feature Envy | 0.154 | 0.333 | 0.211 | 0.186 | 0.871 | 0.145 | no |

The main findings are:

1. **Data Class** is the clearest gain. CUQA had no Java rule for it. The model
   reaches F1 0.71 and is significantly better than the best single-metric rule.
2. **Long Method** is detected well, but a single tuned threshold on method
   length does equally well. The model does not beat it.
3. **Blob** is detected with moderate accuracy. The model scores higher than the
   existing CUQA rule, but the test set is too small to prove the difference.
4. **Feature Envy** could not be learned from this data. It is trained and
   evaluated, but switched off in the shipped model.

The model file is 174 KB and is scored in pure Python. The CUQA service needs no
new dependency.

## 2. Why the 120k dataset was not used

The folder `agents/rdp_agent/datasets` contains
`code_smells_refactoring_dataset_120k.csv` and three subsets of it. This file
was checked first, because it is the largest dataset in the repository. It
cannot be used to train a detector, for these reasons:

- No column is related to the smell label. A one-way ANOVA of every numeric
  column against the label gives p-values between 0.13 and 0.83. Chi-squared
  tests of language, framework, severity and suggested refactoring against the
  label give p-values between 0.67 and 0.80. Only `refactoring_applied` gives
  p = 0.05, and one such value is expected by chance when 15 tests are run.
- A Random Forest reaches 6.9% accuracy and a gradient boosting classifier 6.7%
  on 15 classes, with a spread of 0.2 percentage points between folds. Random
  guessing gives 6.7%.
- The numeric columns are not related to each other either. The largest
  correlation between any two of them is 0.007. In real code, lines of code and
  number of methods are strongly related.
- Every file name ends in `.py`, including the rows labeled Java, C++, C#, Go
  and JavaScript. Java rows are paired with Flask and Django as often as with Spring.

The dataset is therefore randomly generated. A model trained on it would learn
nothing, and any accuracy reported from it would be meaningless. It also has no
source code, so it could not be applied to an uploaded repository in any case.

The check can be repeated with `python training/check_rdp_dataset.py`.

## 3. Dataset

### 3.1 MLCQ

The model is trained on `evaluation/data/MLCQCodeSmellSamples.csv`, the MLCQ
data set by Madeyski and Lewowski [1]. It contains 14,739 reviews of 4,770 Java code samples from 522
open-source repositories, written by 26 reviewers. Each review gives one smell a
severity of none, minor, major or critical. The four smells are Blob, Data Class
(both for classes), Long Method and Feature Envy (both for methods).

MLCQ stores only the labels and a GitHub link for every sample. The code itself
is not in the file. Therefore `training/fetch_mlcq_sources.py` downloads each
referenced file at the exact commit given in the dataset.

| Step | Count |
|---|---:|
| Unique source files referenced | 4,560 |
| Files downloaded | 4,156 (91.1%) |
| Files no longer on GitHub | 397 |
| Files skipped because larger than 3 MB | 7 |
| Samples in MLCQ | 4,770 |
| Samples whose file was unavailable | 478 |
| Samples in files that could not be parsed | 15 |
| Samples matched to a class or method | 4,277 (89.7%) |

Most of the missing files belong to Eclipse projects that have since been moved
or removed. Every sample whose file was available and could be parsed was
matched to exactly one class or method. The match uses the line range, with a
tolerance of two lines, and the names agreed in all cases.

### 3.2 Labels

A sample is positive for a smell when more than half of its reviewers gave a
severity other than "none". A tie counts as negative. This is the same rule that
`evaluation/ground_truth_loader.py` already uses.

| Smell | Samples | With smell | Share |
|---|---:|---:|---:|
| Long Method | 2,170 | 233 | 10.7% |
| Data Class | 2,106 | 277 | 13.2% |
| Blob | 2,103 | 220 | 10.5% |
| Feature Envy | 2,159 | 59 | 2.7% |

The classes are unbalanced, and Feature Envy has very few positive samples. This
matters for the results and is discussed in Section 7.

### 3.3 Features

`src/ml_detector/java_features.py` parses a file with `javalang` and measures
every class and every method. There are 51 features for a method and 49 for a
class. They fall into these groups:

| Group | Examples |
|---|---|
| Size | lines, code lines, comment lines, tokens, identifiers, statements |
| Complexity | cyclomatic complexity, nesting depth, branches, loops, switch cases |
| Data access | accesses to own fields, calls on parameters, on locals, on fields, on other types |
| Coupling | foreign calls, foreign accessor calls, distinct foreign targets, distinct types used |
| Cohesion | tight class cohesion (TCC), share of methods that use a field |
| Accessors | getters, setters, accessor ratio, weight of class |
| Declaration | static, public, abstract, constructor, interface, nested, superclass |

These are measurements only. No threshold is attached to any of them. The same
extractor runs during training and when CUQA analyses an uploaded repository, so
the model always receives features that were computed the same way.

## 4. Model

There is one binary classifier per smell. Each is a gradient-boosted ensemble of
150 decision trees of depth 2, with learning rate 0.05 and subsample 0.8. The
classifier outputs a probability. An entity is reported when its probability is
at or above a threshold that is also learned from the training data.

This model was chosen for three reasons:

- It was about as accurate as the alternatives (Section 6.1).
- It is small. All four classifiers together are 174 KB.
- It can be exported to plain numbers and scored without a library.

The trained model is exported to `src/ml_detector/artifacts/smell_model.json`.
`src/ml_detector/model.py` scores it with the Python standard library. The
training script compares the exported model with scikit-learn on every test
sample. The largest difference in probability is 1.1e-16, so the two are equal.

## 5. Evaluation protocol

1. **Split by repository.** The repositories are split 80/20 with a fixed seed.
   This gives 398 training and 100 test repositories. No repository is in both
   parts. Therefore the test score shows how the model behaves on a project it
   has never seen, which is the real situation when a user uploads a system.
   Samples with exactly the same code were reduced to one sample first.
2. **Model comparison.** On the training repositories only, Logistic
   Regression, Random Forest and Gradient Boosting were compared with 5-fold
   cross-validation grouped by repository, repeated 3 times.
3. **Threshold.** The decision threshold is the value that maximizes F1 on the
   out-of-fold predictions of the training repositories. The test set is not
   used for this.
4. **Test.** The final model is trained on all training repositories and
   evaluated once on the test repositories.
5. **Confidence intervals.** The 95% intervals come from a bootstrap with 1,000
   resamples. Whole repositories are resampled, because samples from the same
   repository are not independent.
6. **Baselines.** The model is compared with (a) the existing CUQA Java rules,
   run unchanged on the same test files, and (b) the best single-metric
   threshold, which is the strongest rule of the form "metric >= t" that can be
   found on the training data.
7. **Shipping rule.** A smell is enabled in the shipped model only if its
   cross-validated F1 on the training repositories is at least 0.50. This bar
   was fixed in the training script before the test results were produced.

| Smell | Train samples | Train positive | Test samples | Test positive |
|---|---:|---:|---:|---:|
| Long Method | 1,813 | 189 | 354 | 44 |
| Feature Envy | 1,803 | 47 | 353 | 12 |
| Blob | 1,723 | 176 | 379 | 44 |
| Data Class | 1,726 | 221 | 379 | 56 |

## 6. Results

### 6.1 Model comparison (cross-validation on training repositories)

| Smell | Model | PR-AUC | ROC-AUC | Best F1 |
|---|---|---:|---:|---:|
| Long Method | Logistic Regression | 0.842 ± 0.007 | 0.972 | 0.765 |
| Long Method | Random Forest | 0.853 ± 0.006 | 0.979 | 0.770 |
| Long Method | Gradient Boosting | 0.854 ± 0.009 | 0.980 | 0.776 |
| Feature Envy | Logistic Regression | 0.143 ± 0.011 | 0.831 | 0.247 |
| Feature Envy | Random Forest | 0.167 ± 0.010 | 0.889 | 0.304 |
| Feature Envy | Gradient Boosting | 0.160 ± 0.030 | 0.876 | 0.289 |
| Blob | Logistic Regression | 0.465 ± 0.004 | 0.885 | 0.500 |
| Blob | Random Forest | 0.527 ± 0.007 | 0.905 | 0.547 |
| Blob | Gradient Boosting | 0.519 ± 0.006 | 0.900 | 0.530 |
| Data Class | Logistic Regression | 0.636 ± 0.002 | 0.905 | 0.610 |
| Data Class | Random Forest | 0.644 ± 0.003 | 0.912 | 0.609 |
| Data Class | Gradient Boosting | 0.612 ± 0.010 | 0.901 | 0.609 |

Random Forest and Gradient Boosting are within about 0.03 PR-AUC of each other
for every smell. Logistic Regression is close for three smells and clearly
weaker for Blob (0.465 against 0.527). The choice between the two tree-based
models is therefore not what limits the accuracy.

### 6.2 Test results of the shipped model

| Smell | Threshold | Precision | Recall | F1 | MCC | ROC-AUC | PR-AUC | TP | FP | FN | TN |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Long Method | 0.504 | 0.829 | 0.659 | 0.734 | 0.707 | 0.982 | 0.902 | 29 | 6 | 15 | 304 |
| Data Class | 0.238 | 0.702 | 0.714 | 0.708 | 0.657 | 0.941 | 0.770 | 40 | 17 | 16 | 306 |
| Blob | 0.248 | 0.458 | 0.614 | 0.524 | 0.458 | 0.904 | 0.574 | 27 | 32 | 17 | 303 |
| Feature Envy | 0.063 | 0.154 | 0.333 | 0.211 | 0.186 | 0.871 | 0.145 | 4 | 22 | 8 | 319 |

95% confidence intervals:

| Smell | Precision | Recall | F1 | MCC | ROC-AUC | PR-AUC |
|---|---|---|---|---|---|---|
| Long Method | [0.70, 0.94] | [0.52, 0.79] | [0.62, 0.82] | [0.59, 0.80] | [0.97, 0.99] | [0.84, 0.95] |
| Data Class | [0.59, 0.81] | [0.58, 0.83] | [0.60, 0.79] | [0.54, 0.75] | [0.91, 0.97] | [0.66, 0.87] |
| Blob | [0.32, 0.58] | [0.47, 0.74] | [0.39, 0.63] | [0.31, 0.58] | [0.87, 0.94] | [0.44, 0.71] |
| Feature Envy | [0.04, 0.29] | [0.08, 0.60] | [0.05, 0.38] | [0.00, 0.36] | [0.80, 0.94] | [0.08, 0.31] |

Accuracy is not used as a headline metric. Only about 11% of the samples have a
smell, so a detector that never reports anything would already be 89% accurate.
F1, MCC and PR-AUC do not have this problem.

Cross-validation on the training repositories gave F1 0.776 for Long Method,
0.609 for Data Class, 0.530 for Blob and 0.289 for Feature Envy. The test
results lie inside or close to these values, so the split was not a lucky one.

Figures: `results/pr_curves.png`, `results/roc_curves.png`,
`results/confusion_matrices.png`.

### 6.3 Comparison with rule-based baselines

| Smell | Detector | Precision | Recall | F1 | MCC |
|---|---|---:|---:|---:|---:|
| Long Method | Existing CUQA rule (more than 30 code lines) | 0.812 | 0.591 | 0.684 | 0.658 |
| Long Method | Best single metric: `loc >= 30` | 0.750 | 0.750 | 0.750 | 0.715 |
| Long Method | ML model | 0.829 | 0.659 | 0.734 | 0.707 |
| Data Class | Existing CUQA rule | none for Java | | | |
| Data Class | Best single metric: `accessor_ratio >= 0.52` | 0.494 | 0.679 | 0.571 | 0.492 |
| Data Class | ML model | 0.702 | 0.714 | 0.708 | 0.657 |
| Blob | Existing CUQA rule (more than 15 methods) | 0.400 | 0.409 | 0.404 | 0.325 |
| Blob | Best single metric: `sloc >= 156` | 0.472 | 0.568 | 0.515 | 0.448 |
| Blob | ML model | 0.458 | 0.614 | 0.524 | 0.458 |
| Feature Envy | Existing CUQA rule | none for Java | | | |
| Feature Envy | Best single metric: `n_calls_on_locals >= 7` | 0.192 | 0.417 | 0.263 | 0.246 |
| Feature Envy | ML model | 0.154 | 0.333 | 0.211 | 0.186 |

To see whether the differences are real, both detectors were scored on the same
bootstrap resamples. The table shows F1 of the ML model minus F1 of the
baseline. If the interval contains 0, the test set cannot separate the two.

| Smell | Baseline | F1 difference | 95% interval | Significant |
|---|---|---:|---|---|
| Long Method | Existing CUQA rule | +0.050 | [-0.04, +0.15] | no |
| Long Method | Best single metric | -0.016 | [-0.12, +0.07] | no |
| Data Class | Best single metric | +0.137 | [+0.05, +0.23] | yes |
| Blob | Existing CUQA rule | +0.120 | [-0.01, +0.26] | no (borderline) |
| Blob | Best single metric | +0.009 | [-0.06, +0.09] | no |
| Feature Envy | Best single metric | -0.053 | [-0.21, +0.11] | no |

Figure: `results/model_vs_baselines.png`.

### 6.4 Most important features

Permutation importance on the test repositories. The number is the drop in
PR-AUC when the feature is shuffled.

- **Long Method:** `loc` (0.026), `sloc` (0.016), `n_branches` (0.010),
  `n_local_vars` (0.009), `n_tokens` (0.008), `max_nesting` (0.006).
- **Data Class:** `accessor_ratio` (0.095), `methods_using_fields_ratio` (0.058),
  `n_distinct_types` (0.025), `n_static_fields` (0.022), `fields_per_method` (0.013).
- **Blob:** `n_foreign_accessor_calls` (0.029), `n_distinct_types` (0.029),
  `total_method_sloc` (0.014), `n_static_methods` (0.007).
- **Feature Envy:** `n_foreign_calls` (0.070). All other features are close to 0.

Figure: `results/feature_importance.png`.

### 6.5 Agreement between the human reviewers

Some MLCQ samples were reviewed by more than one person. For these, the table
shows how often two reviewers of the same sample agree.

| Smell | Review pairs | Observed agreement | Positive agreement (F1) | Cohen's kappa |
|---|---:|---:|---:|---:|
| Long Method | 1,495 | 0.787 | 0.809 | 0.567 |
| Data Class | 2,903 | 0.702 | 0.664 | 0.396 |
| Blob | 2,909 | 0.674 | 0.577 | 0.312 |
| Feature Envy | 1,471 | 0.513 | 0.305 | -0.071 |

These values are not directly comparable to the model's F1, because samples with
several reviews contain more smells than the dataset as a whole. They are still
useful as a reference for how clear each smell is to people.

### 6.6 Runtime cost

Measured on 150 real Java files (60,931 lines, 4,667 classes and methods):

| | Time |
|---|---:|
| Rule-based report only | 21.2 s |
| Rule-based report plus ML pass | 32.5 s |
| ML pass alone | 64 ms per file |

The ML pass adds about 53% to the time of a Java quality report. Two thirds of
that is parsing the file. Scoring itself is fast. Python and C files are not
affected. Setting `CUQA_SMELL_DETECTOR=off` removes the cost completely.

## 7. Discussion

**Long Method.** This smell is detected reliably. The ROC-AUC of 0.98 means the
model ranks almost every long method above almost every normal one. However, the
best single-metric threshold, `loc >= 30`, reaches the same F1. The model's most
important features are also length measures. Therefore, for Long Method the
model does not find anything that a well-chosen length limit misses. What the
data adds is the evidence for the limit: the threshold learned from the expert
labels is 30 lines, which is almost the same as the 30 code lines that the
existing CUQA rule already used. The existing rule scores a little lower (F1
0.68) because its recall is lower. It misses 18 of the 44 long methods in the
test set. Three of them are constructors, which the rule does not check, and
most of the others are methods of 20 to 40 lines whose code lines stay at or
below 30 once blank and comment lines are left out. The difference to the model
is not statistically significant.

**Data Class.** This is where the model clearly helps. CUQA had no Java rule for
Data Class, so this is a new capability. A single metric is not enough here: the
best one, the share of methods that are getters or setters, reaches F1 0.57. The
model reaches 0.71 by combining the accessor ratio with cohesion and coupling
features, and the difference is significant. This matches the definition of the
smell, which is a combination of conditions: many accessors, little behavior and
few collaborators.

**Blob.** Detection is moderate. Less than half of the reported classes (46%)
were also called a Blob by the reviewers. The model does better than the
existing rule of "more than 15 methods" (F1 0.52 against 0.40), but the interval
of the difference just includes zero, so this cannot be claimed with confidence.
It is equal to a tuned size threshold. One reason for the low ceiling is that the
reviewers themselves do not agree well on this smell (kappa 0.31). A model
cannot be more consistent than its labels.

**Feature Envy.** The model failed on this smell, and this is reported as a result.
There are two reasons. First, only 59 samples are positive, and 12 of them are
in the test set. Second, the reviewers do not agree on it at all. Cohen's kappa
is -0.07, which is the level of chance. There is no stable signal to learn. For
this reason Feature Envy is trained and evaluated but disabled in the shipped
model. It can be switched on with `CUQA_ML_INCLUDE_EXPERIMENTAL=1` for
experiments, but its output should not be passed to the refactoring agents.

**What limits the accuracy.** The two tree-based model families perform the same,
and the models are close to the level of agreement between reviewers for Long Method,
Data Class and Blob. Therefore a larger or more complex model is unlikely to
help. More labeled samples and clearer labels would.

**Rule-based or learned.** The results do not show that the learned detector is
better than rules in every case. They show that it is at least as good as the
existing rules for the two smells that have a rule, that it adds one smell that
had none, and that its thresholds come from expert-labeled data and not from a
value chosen by hand. That last point also makes the detector testable: its
precision and recall are now measured, which was not possible for the rules
before.

## 8. Limitations

- **Java only.** MLCQ contains only Java. The model is not applied to Python or
  C files. Those are still analysed by the rules.
- **Four smells, three shipped.** The rules cover more smell types than the
  model. The model does not replace the whole rule set.
- **Small number of positive test samples.** There are 44 to 56 positive test
  samples per shipped smell. The confidence intervals are wide for this reason,
  especially for Blob.
- **Subjective labels.** The labels are opinions of reviewers. Agreement is
  moderate for Long Method and low for Blob.
- **Review process.** In MLCQ, samples with only one review are almost always
  negative, and positive samples were reviewed several times. The labels
  therefore depend on how the reviews were assigned. Review counts are not used
  as a feature, so the model cannot exploit this.
- **Missing files.** 9% of the referenced files are no longer on GitHub. They
  come mostly from a few Eclipse projects, so those projects are under-represented.
- **Parser.** `javalang` supports Java up to version 8. Files that use newer
  syntax, such as records or switch expressions, cannot be parsed. For such a
  file the report says `parse_failed` and falls back to the rules. In the Apache
  Ant 1.8.4 test repository this happened for 3 of 200 files.
- **Threshold choice.** The thresholds maximize F1. A user who wants fewer false
  alarms would need a higher threshold, with lower recall. The precision-recall
  curves show the trade-off.
- **One split.** The test result comes from one 80/20 split. The
  cross-validation on the training repositories is the second estimate.
- **Downstream agents.** In `hybrid` and `ml` mode the report can contain the
  type `DataClass` for Java. The RDP agent's type map has no entry for it, so
  this should be checked with the RDP owner before one of these modes is made
  the default.

## 9. Use in CUQA

The detector is in `src/ml_detector/`. The default behavior of CUQA is
unchanged: `code_smells`, the quality score and the summary are still produced
by the rules. Each Java file report gets an additional `ml_analysis` block with
the model's findings. The modes `hybrid` and `ml` merge the findings into
`code_smells`. The mode `off` skips the model. See the CUQA `README.md` for the
API.

All 297 tests that existed before still pass. 75 tests were added for the
feature extractor, the model, the detector and the API, which gives 372 in total.

## 10. Reproducing the results

```bash
pip install -r training/requirements-training.txt
python training/fetch_mlcq_sources.py
python training/build_dataset.py
python training/train_model.py
```

All random steps use fixed seeds. The dataset was built twice and the training
was run several times. The feature tables, the metrics and the model file were
identical each time.

## References

[1] L. Madeyski and T. Lewowski, "MLCQ: Industry-Relevant Code Smell Data Set,"
in Proceedings of the Evaluation and Assessment in Software Engineering
conference (EASE), 2020.
