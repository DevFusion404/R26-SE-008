# SCTVA Qwen Model Evaluation

This report evaluates model-generated candidates only. Rule-based fallback output is excluded from model-success metrics.

## Overall Classification

| Cases | Accuracy | Precision | Recall | Specificity | F1 | TP/FP/TN/FN | Positive model attempt rate |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 80 | 55.0% | 100.0% | 10.0% | 100.0% | 18.2% | 4/0/40/36 | 42.5% |

## Language Results

| Language | Cases | Accuracy | Precision | Recall | F1 | TP/FP/TN/FN | Candidate yield | Checkpoint pass | Avg generation |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| C | 22 | 59.1% | 100.0% | 18.2% | 30.8% | 2/0/11/9 | 40.0% | 100.0% | 11.919s |
| Java | 34 | 52.9% | 100.0% | 5.9% | 11.1% | 1/0/17/16 | 20.8% | 20.0% | 5.771s |
| Python | 24 | 54.2% | 100.0% | 8.3% | 15.4% | 1/0/12/11 | 33.3% | 33.3% | 7.472s |

## Action Results

| Language | Action | Cases | Accuracy | Recall | F1 | TP/FP/TN/FN |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| C | encapsulate_c_variable | 2 | 50.0% | 0.0% | 0.0% | 0/0/1/1 |
| C | extract_c_component | 2 | 50.0% | 0.0% | 0.0% | 0/0/1/1 |
| C | extract_method | 2 | 50.0% | 0.0% | 0.0% | 0/0/1/1 |
| C | introduce_c_parameter_object | 2 | 50.0% | 0.0% | 0.0% | 0/0/1/1 |
| C | introduce_constant | 2 | 100.0% | 100.0% | 100.0% | 1/0/1/0 |
| C | move_c_function | 2 | 50.0% | 0.0% | 0.0% | 0/0/1/1 |
| C | remove_dead_code | 2 | 50.0% | 0.0% | 0.0% | 0/0/1/1 |
| C | replace_nested_conditional_with_guard_clauses | 2 | 50.0% | 0.0% | 0.0% | 0/0/1/1 |
| C | replace_parameter_with_method_call | 2 | 50.0% | 0.0% | 0.0% | 0/0/1/1 |
| C | replace_temp_with_query | 2 | 50.0% | 0.0% | 0.0% | 0/0/1/1 |
| C | replace_unsafe_function | 2 | 100.0% | 100.0% | 100.0% | 1/0/1/0 |
| Java | collapse_hierarchy | 2 | 50.0% | 0.0% | 0.0% | 0/0/1/1 |
| Java | extract_java_class | 2 | 50.0% | 0.0% | 0.0% | 0/0/1/1 |
| Java | extract_method | 2 | 50.0% | 0.0% | 0.0% | 0/0/1/1 |
| Java | extract_subclass | 2 | 50.0% | 0.0% | 0.0% | 0/0/1/1 |
| Java | hide_delegate | 2 | 50.0% | 0.0% | 0.0% | 0/0/1/1 |
| Java | inline_java_class | 2 | 50.0% | 0.0% | 0.0% | 0/0/1/1 |
| Java | introduce_constant | 2 | 100.0% | 100.0% | 100.0% | 1/0/1/0 |
| Java | introduce_facade | 2 | 50.0% | 0.0% | 0.0% | 0/0/1/1 |
| Java | introduce_java_parameter_object | 2 | 50.0% | 0.0% | 0.0% | 0/0/1/1 |
| Java | move_java_method | 2 | 50.0% | 0.0% | 0.0% | 0/0/1/1 |
| Java | pull_up_method | 2 | 50.0% | 0.0% | 0.0% | 0/0/1/1 |
| Java | remove_dead_code | 2 | 50.0% | 0.0% | 0.0% | 0/0/1/1 |
| Java | rename_method | 2 | 50.0% | 0.0% | 0.0% | 0/0/1/1 |
| Java | replace_conditional_with_polymorphism | 2 | 50.0% | 0.0% | 0.0% | 0/0/1/1 |
| Java | replace_data_value_with_object | 2 | 50.0% | 0.0% | 0.0% | 0/0/1/1 |
| Java | replace_parameter_with_method_call | 2 | 50.0% | 0.0% | 0.0% | 0/0/1/1 |
| Java | replace_temp_with_query | 2 | 50.0% | 0.0% | 0.0% | 0/0/1/1 |
| Python | extract_method | 2 | 50.0% | 0.0% | 0.0% | 0/0/1/1 |
| Python | extract_python_class | 2 | 50.0% | 0.0% | 0.0% | 0/0/1/1 |
| Python | hide_delegate | 2 | 50.0% | 0.0% | 0.0% | 0/0/1/1 |
| Python | inline_python_class | 2 | 50.0% | 0.0% | 0.0% | 0/0/1/1 |
| Python | introduce_constant | 2 | 50.0% | 0.0% | 0.0% | 0/0/1/1 |
| Python | introduce_python_parameter_object | 2 | 50.0% | 0.0% | 0.0% | 0/0/1/1 |
| Python | move_python_method | 2 | 50.0% | 0.0% | 0.0% | 0/0/1/1 |
| Python | narrow_exception_handler | 2 | 100.0% | 100.0% | 100.0% | 1/0/1/0 |
| Python | remove_dead_code | 2 | 50.0% | 0.0% | 0.0% | 0/0/1/1 |
| Python | rename_method | 2 | 50.0% | 0.0% | 0.0% | 0/0/1/1 |
| Python | replace_conditional_with_polymorphism | 2 | 50.0% | 0.0% | 0.0% | 0/0/1/1 |
| Python | replace_nested_conditional_with_guard_clauses | 2 | 50.0% | 0.0% | 0.0% | 0/0/1/1 |

## Definitions

- **Candidate yield**: source-code candidates generated divided by model inference attempts.
- **Eligible cases**: cases that passed SCTVA's safety preconditions and reached model inference.
- **Checkpoint pass**: candidates passing action-level SCTVA syntax, structural, behavioral, and invariant checks.
- **Positive case**: a valid, executable RDP action/target pair that the model should transform.
- **Negative case**: an intentionally invalid or absent target that the model should not transform.
- **Accuracy/F1**: binary model-acceptance metrics. A positive prediction requires model output to survive final SCTVA validation; rule fallback is excluded.
- **Rule-based fallback**: may complete the SCTVA request safely, but is never credited as model success.
- **TP/FP/TN/FN**: true positive, false positive, true negative, and false negative counts.
