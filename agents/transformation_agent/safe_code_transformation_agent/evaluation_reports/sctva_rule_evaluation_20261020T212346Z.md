# SCTVA Rule-Based Transformation Evaluation

This report evaluates deterministic rule transformations only. A positive result requires the requested rule action to survive final SCTVA validation.

## Overall Classification

| Cases | Accuracy | Precision | Recall | Specificity | F1 | TP/FP/TN/FN |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 56 | 85.7% | 95.5% | 75.0% | 96.4% | 84.0% | 21/1/27/7 |

## Language Results

| Language | Cases | Accuracy | Precision | Recall | F1 | Validation pass | Rule transform | Avg execution |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| C | 20 | 100.0% | 100.0% | 100.0% | 100.0% | 65.0% | 50.0% | 0.048s |
| Java | 14 | 92.9% | 100.0% | 85.7% | 92.3% | 57.1% | 42.9% | 8.739s |
| Python | 22 | 68.2% | 83.3% | 45.5% | 58.8% | 54.5% | 27.3% | 0.022s |

## Refactoring Action Results

| Language | Action | Cases | Accuracy | Precision | Recall | F1 | TP/FP/TN/FN |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| C | encapsulate_c_variable | 2 | 100.0% | 100.0% | 100.0% | 100.0% | 1/0/1/0 |
| C | extract_c_component | 2 | 100.0% | 100.0% | 100.0% | 100.0% | 1/0/1/0 |
| C | extract_method | 2 | 100.0% | 100.0% | 100.0% | 100.0% | 1/0/1/0 |
| C | introduce_c_parameter_object | 2 | 100.0% | 100.0% | 100.0% | 100.0% | 1/0/1/0 |
| C | introduce_constant | 2 | 100.0% | 100.0% | 100.0% | 100.0% | 1/0/1/0 |
| C | remove_dead_code | 2 | 100.0% | 100.0% | 100.0% | 100.0% | 1/0/1/0 |
| C | replace_nested_conditional_with_guard_clauses | 2 | 100.0% | 100.0% | 100.0% | 100.0% | 1/0/1/0 |
| C | replace_parameter_with_method_call | 2 | 100.0% | 100.0% | 100.0% | 100.0% | 1/0/1/0 |
| C | replace_temp_with_query | 2 | 100.0% | 100.0% | 100.0% | 100.0% | 1/0/1/0 |
| C | replace_unsafe_function | 2 | 100.0% | 100.0% | 100.0% | 100.0% | 1/0/1/0 |
| Java | extract_java_class | 2 | 100.0% | 100.0% | 100.0% | 100.0% | 1/0/1/0 |
| Java | extract_method | 2 | 100.0% | 100.0% | 100.0% | 100.0% | 1/0/1/0 |
| Java | hide_delegate | 2 | 100.0% | 100.0% | 100.0% | 100.0% | 1/0/1/0 |
| Java | introduce_constant | 2 | 100.0% | 100.0% | 100.0% | 100.0% | 1/0/1/0 |
| Java | introduce_java_parameter_object | 2 | 100.0% | 100.0% | 100.0% | 100.0% | 1/0/1/0 |
| Java | remove_dead_code | 2 | 50.0% | N/A | 0.0% | 0.0% | 0/0/1/1 |
| Java | rename_method | 2 | 100.0% | 100.0% | 100.0% | 100.0% | 1/0/1/0 |
| Python | extract_method | 2 | 100.0% | 100.0% | 100.0% | 100.0% | 1/0/1/0 |
| Python | extract_python_class | 2 | 50.0% | N/A | 0.0% | 0.0% | 0/0/1/1 |
| Python | hide_delegate | 2 | 50.0% | N/A | 0.0% | 0.0% | 0/0/1/1 |
| Python | inline_python_class | 2 | 50.0% | N/A | 0.0% | 0.0% | 0/0/1/1 |
| Python | introduce_constant | 2 | 100.0% | 100.0% | 100.0% | 100.0% | 1/0/1/0 |
| Python | introduce_python_parameter_object | 2 | 100.0% | 100.0% | 100.0% | 100.0% | 1/0/1/0 |
| Python | move_python_method | 2 | 50.0% | N/A | 0.0% | 0.0% | 0/0/1/1 |
| Python | narrow_exception_handler | 2 | 50.0% | 50.0% | 100.0% | 66.7% | 1/1/0/0 |
| Python | remove_dead_code | 2 | 100.0% | 100.0% | 100.0% | 100.0% | 1/0/1/0 |
| Python | rename_method | 2 | 50.0% | N/A | 0.0% | 0.0% | 0/0/1/1 |
| Python | replace_conditional_with_polymorphism | 2 | 50.0% | N/A | 0.0% | 0.0% | 0/0/1/1 |

## Definitions

- **Positive case**: a valid, executable RDP action/target pair that a deterministic rule should transform.
- **Negative case**: an intentionally invalid or absent target that should remain unchanged.
- **Validation pass**: the final SCTVA syntax, structural, behavioral, and invariant decision passed without rollback.
- **Rule transform**: a rule-based output changed the source and was retained after final SCTVA validation.
- **Accuracy/F1**: binary rule-acceptance metrics over the same positive and negative corpus used for Qwen evaluation.
