# CUQA Code Smell Detection Evaluation Report
**Timestamp**: `2026-08-30T06:15:29.367825+00:00`  
**Evaluated Repositories**: `1` | **Evaluated Samples N**: `9517`  
**Evaluation Mode**: `Real-World Empirical Evaluation`

## Executive Performance Summary
```
CUQA Detection Evaluation Summary
--------------------------------------------------
Language   Precision  Recall     Macro-F1   N     
--------------------------------------------------
Java       0.0000     0.0000     N/A        14764 
--------------------------------------------------
Overall Macro-F1: N/A (Pending Ground Truth)
```

## Detailed Performance breakdown by Smell Type
| Smell Type | TP | FP | FN | TN | Precision | Recall | F1 | MCC |
|---|---|---|---|---|---|---|---|---|
| `Comments` | 0 | 629 | 0 | 0 | 0.0000 | N/A | N/A | N/A |
| `DataClass` | 0 | 0 | 301 | 2037 | N/A | 0.0000 | N/A | N/A |
| `FeatureEnvy` | 0 | 0 | 68 | 2347 | N/A | 0.0000 | N/A | N/A |
| `LargeClass` | 0 | 210 | 254 | 2080 | 0.0000 | 0.0000 | N/A | -0.0999 |
| `LongMethod` | 0 | 632 | 276 | 2154 | 0.0000 | 0.0000 | N/A | -0.1605 |
| `MagicNumber` | 0 | 3746 | 0 | 0 | 0.0000 | N/A | N/A | N/A |
| `TooManyParameters` | 0 | 30 | 0 | 0 | 0.0000 | N/A | N/A | N/A |

## Inter-Rater Reliability (Human Annotation Consensus)
- **Status**: `Calculated`
- **Cohen's Kappa (\kappa)**: `0.3689`
- **Observed Agreement**: `0.675`
- **Disagreements**: `802` / `2468`