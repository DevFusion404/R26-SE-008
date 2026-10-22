# Preprocessed datasets of the CUQA smell models

Written by `training/export_preprocessed.py`. Do not edit by hand; rerun the
script instead. Every file is one smell, one row per labelled class or method,
after all preprocessing, exactly as the training scripts use it.

| Model | Folder | Smell file | Entity | Train samples (positive) | Test samples (positive) |
|---|---|---|---|---:|---:|
| 1 (MLCQ) | `mlcq/` | `long_method.csv` | method | 1,813 (189) | 354 (44) |
| 1 (MLCQ) | `mlcq/` | `feature_envy.csv` | method | 1,803 (47) | 353 (12) |
| 1 (MLCQ) | `mlcq/` | `blob.csv` | class | 1,723 (176) | 379 (44) |
| 1 (MLCQ) | `mlcq/` | `data_class.csv` | class | 1,726 (221) | 379 (56) |
| 2 (DACOS) | `dacos/` | `complex_method.csv` | method | 39,872 (1,358) | 17,229 (285) |
| 2 (DACOS) | `dacos/` | `long_parameter_list.csv` | method | 39,190 (598) | 16,877 (197) |
| 2 (DACOS) | `dacos/` | `multifaceted_abstraction.csv` | class | 5,591 (900) | 2,533 (364) |

`summary.csv` has the same counts plus the number of repositories and, for
DACOS, the human-annotated samples. MLCQ has 398 training and 100 test
repositories; DACOS has 8 and 2.

## Preprocessing steps

**Model 1, MLCQ** (Madeyski and Lewowski, 2020)

1. The MLCQ CSV holds expert reviews and a GitHub link per sample, no code.
   Each referenced file was downloaded at the exact commit
   (`fetch_mlcq_sources.py`): 4,156 of 4,560 files; the rest are gone from GitHub.
2. Each file was parsed with `javalang` and every class and method measured by
   `src/ml_detector/java_features.py`, the same extractor CUQA uses at runtime.
3. Each sample was matched to the class or method whose line range agrees within
   2 lines (`build_dataset.py`): 4,277 of 4,770 samples matched.
4. Label: 1 when more than half of the reviewers gave a severity other than
   "none", else 0 (a tie is 0).
5. Copies of the same code in several repositories (same token fingerprint) were
   kept once.
6. Split: the repositories were shuffled with seed 42 and 20% held out for test.
   No repository is in both parts.

**Model 2, DACOS** (Nandani, Saad and Sharma, 2023)

1. The DACOS MySQL dumps and code snippets were read directly
   (`build_dacos_dataset.py`).
2. Methods were measured inside their enclosing class snippet (matched by token
   fingerprint), so the class-context features are real; classes were measured
   from their own snippet.
3. Label, with its source in `label_source`:
   - `human`: two annotators judged a borderline snippet; 1 when more than half
     marked the smell.
   - `threshold`: a clear case labelled by the DACOS authors with metric
     thresholds (the DACOSX part).
4. Copies of the same code were kept once, preferring a human label.
5. Split: the same seeded 80/20 split by repository as model 1.

**Not done, on purpose:** no scaling or normalisation (tree models compare a
value with a split point, so scale does not matter), no oversampling (class
imbalance is handled by the learned decision threshold and by imbalance-aware
metrics), no missing-value filling (every feature is always computed).
Decimal values are rounded to 6 places.

## Columns

### Sample and label columns

| Column | Model | Meaning |
|---|---|---|
| `sample_id` | both | ID of the sample in the original dataset |
| `repository` | both | Source repository (MLCQ: git URL; DACOS: project name) |
| `commit_hash` | 1 | Commit the file was downloaded at |
| `path` | both | File path in the repository (DACOS: path of the snippet file) |
| `entity_name` | both | Class or method name |
| `type_kind` | both | `class`, `interface`, `enum`, `method` or `constructor` |
| `start_line`, `end_line` | both | Line span of the entity |
| `split` | both | `train` or `test` |
| `label` | both | 1 = smell, 0 = no smell (the target) |
| `label_source` | 2 | `human` or `threshold` (see above) |
| `n_reviews` | both | Reviews / annotations of this sample (0 for threshold labels) |
| `n_positive_reviews` | both | How many of them marked the smell |
| `mean_severity` | 1 | Mean MLCQ severity, 0 = none, 1 = minor, 2 = major, 3 = critical |

### Method features (51; in the method-level files)

| Group | Feature | Meaning |
|---|---|---|
| Size | `loc` | Lines from the declaration (with annotations) to the closing brace |
| | `sloc` | Lines that hold code |
| | `comment_lines` | Lines with text but no code |
| | `n_tokens` | Tokens |
| | `n_identifiers`, `n_distinct_identifiers` | Identifier tokens, and distinct identifiers |
| | `n_operators`, `n_literals` | Operator tokens, literal tokens |
| Control flow | `n_statements` | Statements (blocks not counted) |
| | `cyclomatic_complexity` | 1 + ifs + loops + case labels + catches + ternaries + `&&`/`\|\|` |
| | `max_nesting` | Deepest nesting of if/loop/try/switch/synchronized (else-if is not deeper) |
| | `n_params`, `n_local_vars` | Parameters, local variables |
| | `n_branches` | Ifs + case labels + ternaries |
| | `n_loops`, `n_switch_cases`, `n_try`, `n_catch` | Loops, case labels, try blocks, catch clauses |
| | `n_returns`, `n_throws`, `n_assignments` | Return, throw and assignment statements |
| | `n_bool_ops`, `n_lambdas`, `n_object_creations` | `&&`/`\|\|`, lambdas, `new X()` |
| Calls and data access | `n_method_calls`, `n_distinct_calls` | Calls, and distinct method names called |
| | `max_chain_length` | Longest chain of calls, e.g. `a.b().c()` = 2 |
| | `n_own_calls`, `n_own_field_access` | Calls on this/super/unqualified; uses of the class's own fields |
| | `n_foreign_calls` | Calls on any other object or type |
| | `n_foreign_accessor_calls` | Of those, calls to get*/set*/is* |
| | `n_foreign_field_access` | Direct field reads of other objects |
| | `n_static_refs` | References like `Type.FIELD` |
| | `n_calls_on_params`, `n_calls_on_locals`, `n_calls_on_fields`, `n_calls_on_types` | Calls on parameters, locals, own field objects, types (static calls) |
| | `n_distinct_foreign_targets` | Distinct variables/fields whose members are used |
| | `max_calls_one_target` | Uses of the most-used foreign target |
| | `foreign_access_ratio` | Foreign accesses / all accesses |
| Declaration | `is_static`, `is_constructor`, `is_public`, `is_private`, `has_body` | 1 or 0 |
| | `is_accessor_name` | Name starts with get/set/is |
| | `returns_void` | Method returns void |
| Enclosing class | `class_n_methods`, `class_n_fields`, `class_sloc` | Size of the class the method is in |
| | `sloc_share_of_class` | Method code lines / class code lines |

### Class features (49; in the class-level files)

| Group | Feature | Meaning |
|---|---|---|
| Size | `loc`, `sloc`, `comment_lines`, `n_tokens` | As for methods, over the whole class |
| | `n_methods`, `n_constructors`, `n_fields`, `n_inner_types` | Declared members |
| Visibility | `n_public_methods`, `n_private_methods`, `n_static_methods`, `n_abstract_methods` | Methods by modifier (abstract = no body) |
| | `n_public_fields`, `n_private_fields`, `n_static_fields`, `n_final_fields` | Fields by modifier |
| Accessors | `n_getters` | get*/is* methods with no parameters and at most 1 statement |
| | `n_setters` | set* methods with 1 parameter and at most 2 statements |
| | `accessor_ratio` | (getters + setters) / methods |
| | `n_non_accessor_methods` | Methods that are not getters or setters |
| | `weight_of_class` | WOC: public non-accessor methods / public members |
| Complexity | `wmc` | Sum of the cyclomatic complexity of all methods and constructors |
| | `avg_cc`, `max_cc` | Mean and largest method complexity |
| | `total_method_sloc`, `avg_method_sloc`, `max_method_sloc` | Method code lines |
| | `avg_method_params`, `max_method_params` | Method parameters |
| | `max_nesting`, `n_statements`, `n_method_calls` | Over all methods |
| Coupling | `n_own_field_access`, `n_foreign_calls`, `n_foreign_accessor_calls`, `n_foreign_field_access` | Sums over the methods (see method features) |
| | `n_distinct_foreign_targets` | Distinct foreign targets over all methods |
| | `n_distinct_types` | Distinct type names referenced in the class |
| | `file_n_imports` | Import statements in the file |
| Cohesion | `tcc` | Tight class cohesion: share of method pairs that use a common field |
| | `methods_using_fields_ratio` | Share of methods that use at least one field |
| | `fields_per_method` | Fields / methods |
| Declaration | `has_superclass`, `n_interfaces`, `is_interface`, `is_enum`, `is_abstract`, `is_nested`, `n_overrides` | Inheritance and kind; `n_overrides` = methods with `@Override` |

## Using the files

The model was trained on the `train` rows with every feature column as input
and `label` as the target, and evaluated once on the `test` rows. Example:

```python
import pandas as pd
t = pd.read_csv("mlcq/data_class.csv", keep_default_na=False)  # keeps a class called "None"
features = t.columns[t.columns.get_loc("mean_severity") + 1:]   # DACOS: after "n_positive_reviews"
train, test = t[t.split == "train"], t[t.split == "test"]
X_train, y_train = train[features], train["label"]
```

For DACOS, judge results on the `human` rows separately: the `threshold` rows
are easy by construction.
