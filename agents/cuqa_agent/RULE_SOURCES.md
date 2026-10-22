# CUQA rule engine: where the rules are and where they come from

This document lists every rule-based smell detector in CUQA, where its code is,
and the published source for the smell and for its threshold. Each source was
checked on 21 October 2026; the links go to the publisher, the tool's official
documentation or the tool's source code.

Where a threshold has no published value, the document says so and gives the
closest published value and, where it exists, CUQA's own measurement.

## 1. Where the rule engine is

All rules run from `generate_file_report()` in `src/report_generator.py`
(line 2248), which picks the detector by file extension. Every finding gets
`"detected_by": "rule"` (`_tag_rule_smells()`, line 2179) and a category from
`SMELL_CATEGORY_MAP` (line 123).

| Language | Code | Rules |
|---|---|---|
| Python | `src/report_generator.py`: `_PythonSmellVisitor` (line 713; `visit_FunctionDef` 748, `visit_ClassDef` 845, `visit_Constant` 998) and `_analyze_python_smells()` (line 1050); helpers `_longest_elif_chain` (218), `_chain_depth` (255), `_detect_unreachable_code` (452), `_detect_unused_variables` (503) | Long Method, Too Many Parameters, Switch Statements, Message Chains, Unreachable Code, Unused Variable, Large Class, Lazy Class, Primitive Obsession, Inappropriate Intimacy, Speculative Generality, Magic Number, Bare Except, Dead Code, Duplicate Code, Feature Envy, Data Clumps |
| Java | `src/report_generator.py`: `_analyze_java_smells()` (line 1634), `_java_literal_is_not_magic()` (1368), `_java_design_smells()` (1465); `src/java_rules.py`: `METRIC_RULES` (line 98), `detect_metric_smells()` (144) | Long Method, Too Many Parameters, Large Class, Magic Number, Message Chains, Switch Statements, Dead Code, Duplicate Code, Data Clumps, Global Variable, Complex Method, Deep Nesting, Feature Envy, Data Class |
| C | `src/report_generator.py`: `_analyze_c_smells()` (line 1913); helpers in `src/c_ast_parser.py`: `_UNSAFE_FUNCS` (129), `_find_functions_regex` (242), `_max_control_nesting` (356), `_find_global_vars_regex` (388), `analyze_c_magic_numbers` (725) | Long Function, Too Many Parameters, Deep Nesting, Duplicate Code, Dead Code, Magic Number, Unsafe Function Usage, Global Variable, Large Header File |
| Python, Java | `_comments_smell()` in `src/report_generator.py` (line 2151) | Comments |

Line numbers are those of 21 October 2026.

## 2. Sources for the smells and the categories

| # | Source | Used for |
|---|---|---|
| [F1] | M. Fowler, *Refactoring: Improving the Design of Existing Code*, Addison-Wesley, 1999. 2nd ed. 2018, Chapter 3 "Bad Smells in Code" ([O'Reilly listing](https://www.oreilly.com/library/view/refactoring-improving-the/9780134757681)) | Definition of almost every smell: Long Method/Function, Large Class, Long Parameter List, Switch Statements, Message Chains, Feature Envy, Data Clumps, Data Class, Lazy Class, Primitive Obsession, Speculative Generality, Inappropriate Intimacy, Duplicated Code, Comments; Global Data (2nd ed.) |
| [M1] | M. V. Mäntylä, J. Vanhanen and C. Lassenius, "A Taxonomy and an Initial Empirical Study of Bad Smells in Code," Proc. Int. Conf. Software Maintenance (ICSM), IEEE, 2003, pp. 381–384 ([record](https://research.fi/en/results/publication/0357437703); [taxonomy summary](https://courses.cs.duke.edu/spring23/compsci308/resources/Code_Smells_Taxonomy.pdf)) | The five categories in `SMELL_CATEGORY_MAP`: Bloaters, Object-Orientation Abusers, Change Preventers, Dispensables, Couplers |
| [LM] | M. Lanza and R. Marinescu, *Object-Oriented Metrics in Practice*, Springer, 2006 | Metric-based detection strategies for God Class, Feature Envy, Data Class |
| [RM] | R. Marinescu, "Detection Strategies: Metrics-Based Rules for Detecting Design Flaws," Proc. IEEE Int. Conf. Software Maintenance (ICSM), 2004 | The idea of a rule as metric thresholds joined by AND/OR |
| [DE] | N. Moha, Y.-G. Guéhéneuc, L. Duchien and A.-F. Le Meur, "DECOR: A Method for the Specification and Detection of Code and Design Smells," *IEEE Trans. Software Eng.*, 36(1):20–36, 2010, doi:10.1109/TSE.2009.50 ([HAL](https://hal.univ-grenoble-alpes.fr/INRIA-RENNES/inria-00538476v1)) | Rule-based smell detection as a method, validated by precision and recall |
| [MC] | T. J. McCabe, "A Complexity Measure," *IEEE Trans. Software Eng.*, SE-2(4):308–320, 1976 ([PDF](https://www.cs.mtsu.edu/~untch/6050/private/McCabe1976.pdf)) | Cyclomatic complexity and the limit of 10 |
| [LH] | K. J. Lieberherr and I. M. Holland, "Assuring Good Style for Object-Oriented Programs," *IEEE Software*, 6(5):38–48, 1989, doi:10.1109/52.35588 | Law of Demeter, behind Message Chains |
| [WS] | W. Wulf and M. Shaw, "Global Variable Considered Harmful," *ACM SIGPLAN Notices*, 8(2):28–34, 1973, doi:10.1145/953353.953355 | Global Variable |
| [BK] | S. Bellon, R. Koschke, G. Antoniol, J. Krinke and E. Merlo, "Comparison and Evaluation of Clone Detection Tools," *IEEE Trans. Software Eng.*, 2007 ([PDF](https://plg.uwaterloo.ca/~migod/846/papers/bellon-tse07.pdf)) | Clone types; CUQA's Duplicate Code finds Type-2 clones (same structure, renamed identifiers and literals) |
| [CWE] | MITRE CWE-242 "Use of Inherently Dangerous Function" and [CWE-676](https://cwe.mitre.org/data/definitions/676) "Use of Potentially Dangerous Function"; [Polyspace CWE-242 checker](https://www.mathworks.com/help/bugfinder/ref/cwe242.html) | Unsafe Function Usage in C |
| [PEP8] | Python PEP 8, "Programming Recommendations" (bare `except:`) | Bare Except |

### Industry tools whose defaults are quoted below

| # | Tool documentation | Checked |
|---|---|---|
| [PMD-D] | [PMD Java "Design" rules](https://docs.pmd-code.org/latest/pmd_rules_java_design.html) | CyclomaticComplexity `methodReportLevel` = 10; AvoidDeeplyNestedIfStmts `problemDepth` = 3; ExcessiveParameterList `minimum` = 10; TooManyMethods `maxmethods` = 10; NcssCount `methodReportLevel` = 60; GodClass cites [LM] p. 80; LawOfDemeter cites [LH] |
| [PMD-S] | PMD source: [`GodClassRule.java`](https://github.com/pmd/pmd/blob/main/pmd-java/src/main/java/net/sourceforge/pmd/lang/java/rule/design/GodClassRule.java), [`DataClassRule.java`](https://github.com/pmd/pmd/blob/main/pmd-java/src/main/java/net/sourceforge/pmd/lang/java/rule/design/DataClassRule.java) | GodClass: WMC ≥ 47, ATFD > 5 ("Few means between 2 and 5", [LM] p. 18), TCC < 1/3. DataClass: WOC < 1/3 and (NOPA+NOAM > 3 and WMC < 31, or NOPA+NOAM > 5 and WMC < 47) |
| [PMD-B] | [PMD Java "Best Practices" rules](https://docs.pmd-code.org/latest/pmd_rules_java_bestpractices.html) | UnusedPrivateMethod, UnusedPrivateField, UnusedLocalVariable |
| [CS] | [Checkstyle MagicNumber](https://checkstyle.org/checks/coding/magicnumber.html) | `ignoreNumbers` default = -1, 0, 1, 2 |
| [PL] | [Pylint options](https://pylint.readthedocs.io/en/stable/user_guide/configuration/all-options.html), [R0913 too-many-arguments](https://pylint.readthedocs.io/en/stable/user_guide/messages/refactor/too-many-arguments.html) | `max-args` = 5, `min-public-methods` = 2, `max-nested-blocks` = 5, `min-similarity-lines` = 4, `max-module-lines` = 1000; messages W0702 bare-except, W0101 unreachable, W0612 unused-variable, R0903 too-few-public-methods |
| [SQ] | [SonarSource RSPEC-134](https://rules.sonarsource.com/cpp/tag/brain-overload/RSPEC-134) | Control-flow nesting: issue raised at depth 4 (default maximum 3) |

## 3. Every rule, its threshold and its evidence

✔ = the threshold equals a published value. ≈ = within the range of published
values. ○ = no published value for this exact threshold.

| Smell | Lang. | CUQA rule | Smell defined in | Threshold evidence | |
|---|---|---|---|---|---|
| Too Many Parameters | Py, Java, C | more than 5 parameters | [F1] Long Parameter List | Pylint `max-args` = 5 [PL] (PMD 10, Sonar 7) | ✔ |
| Complex Method | Java | cyclomatic complexity > 10 | [MC] | McCabe's limit of 10 [MC]; PMD `methodReportLevel` = 10 [PMD-D] | ✔ |
| Deep Nesting | Java, C | control-flow depth > 3 | [F1] (long conditionals) | Sonar S134 maximum 3 [SQ]; PMD `problemDepth` = 3 [PMD-D] (Pylint 5) | ✔ |
| Data Class | Java | WOC < 1/3 and (NOPA+NOAM > 3 and WMC < 31, or > 5 and WMC < 47) | [F1], [LM], [RM] | Same numbers as PMD's implementation of [LM] [PMD-S] | ✔ |
| Feature Envy | Java | ATFD > 5, LAA < 1/3, FDP ≤ 5 | [F1], [LM] | [LM] strategy; "few" = 5 as in PMD [PMD-S]; LAA < 1/3 = [LM]'s "one third" | ✔ |
| Feature Envy | Python | uses ≥ 3 attributes of one parameter/local and more than of its own class | [F1] | ○ CUQA's own threshold | ○ |
| Magic Number | Py, Java | any number except -1, 0, 1, 2 (constants, enums, case labels, unit factors and test files excluded) | [F1] (Replace Magic Number with Symbolic Constant) | Checkstyle default `ignoreNumbers` = -1, 0, 1, 2 [CS] | ✔ |
| Magic Number | C | as above, plus common sizes, ports and HTTP codes | [F1] | ≈ Checkstyle default plus a CUQA list | ≈ |
| Message Chains | Py, Java | a chain of 3 or more (Java: getter calls) | [F1], [LH] | ≈ PMD LawOfDemeter `trustRadius` = 1 [PMD-D] is stricter; 3 is CUQA's | ≈ |
| Dead Code | Java | unused private method / private field | [M1] (Dispensables) | PMD UnusedPrivateMethod, UnusedPrivateField [PMD-B] | ✔ |
| Dead Code | Py, C | Python: module-level function/class or private method never referenced (repository-wide when the whole project is scanned); C: `static` function never called | [M1] | Same idea as [PMD-B] (unused private members), extended to file-scoped C functions | ≈ |
| Unreachable Code | Py | statements after return/raise | [M1] (Dispensables) | Pylint W0101 [PL] | ✔ |
| Unused Variable | Py | assigned, never read | [M1] | Pylint W0612 [PL]; PMD UnusedLocalVariable [PMD-B] | ✔ |
| Bare Except | Py | `except:` without a type | — | [PEP8]; Pylint W0702 [PL] | ✔ |
| Unsafe Function Usage | C | gets, strcpy, strcat, sprintf, scanf | — | gets → CWE-242; strcpy/strcat/sprintf/scanf → CWE-676 [CWE] | ✔ |
| Global Variable | Java, C | Java: public static non-final field; C: file-scope variable | [WS], [F1] 2nd ed. Global Data | ○ no threshold (presence) | ✔ |
| Duplicate Code | Py, Java, C | same body structure (Type-2 clone), at least 3 statements | [F1] Duplicated Code, [BK] | ≈ Pylint `min-similarity-lines` = 4 [PL] | ≈ |
| Data Clumps | Py, Java | same 3+ parameters in two methods | [F1] | ○ CUQA's own threshold | ○ |
| Switch Statements | Py, Java | 4+ else-if branches (Py, Java), switch with 5+ case labels (Java) | [F1] | ○ CUQA's own threshold | ○ |
| Lazy Class | Py | ≤ 2 methods and < 30 lines (data/exception/enum classes excluded) | [F1] | ≈ Pylint `min-public-methods` = 2 (R0903) [PL] | ≈ |
| Long Method | Py, Java | more than 30 code lines | [F1] | ○ No single published value (PMD 60 statements [PMD-D]). CUQA evidence: the best single threshold learned from the MLCQ expert labels is `loc ≥ 30` (`training/MODEL_EVALUATION.md` §6.3) | ○ |
| Long Function | C | more than 40 code lines | [F1] | ○ no published value | ○ |
| Large Class | Py, Java | more than 15 methods | [F1] | ≈ PMD TooManyMethods `maxmethods` = 10 [PMD-D]. Measured on MLCQ: F1 0.40 (`MODEL_EVALUATION.md` §6.3) | ≈ |
| Primitive Obsession | Py | 4+ primitive-typed fields | [F1] | ○ CUQA's own threshold | ○ |
| Inappropriate Intimacy | Py | accesses a private (`_x`) attribute of another object | [F1] | ○ presence rule | ○ |
| Speculative Generality | Py | abstract class with no subclass | [F1] | ○ presence rule | ○ |
| Comments | Py, Java | > 30% explanatory comment lines in a file over 50 lines | [F1] Comments | ○ CUQA's own threshold | ○ |
| Large Header File | C | `.h` file over 300 lines | — | ○ no published value (Pylint `max-module-lines` = 1000 for Python) | ○ |

## 4. What to say about the ○ rows

The smell in every ○ row is a published smell ([F1] or [M1]). Only the exact
number is CUQA's choice. Published tools disagree on these numbers too (for
example, method length: PMD 60 statements, Pylint 50 statements). CUQA picked
values in the same range and measured them where labelled data exists:

- Long Method, more than 30 lines: on MLCQ the learned best threshold is also
  30 lines, F1 0.68 for the CUQA rule (`MODEL_EVALUATION.md` §6.3).
- Large Class, more than 15 methods: F1 0.40 on MLCQ, weaker than the model
  (0.52) and the best size threshold (0.52); this is reported as a limitation.
- Feature Envy (Java), Data Class, Complex Method, Long Parameter List: measured
  on MLCQ or DACOS in `training/results/combined_detector.md`.

The remaining ○ rules have no labelled data, so their precision is not
measured. That is a stated limitation, not a claim that they are accurate.

## 5. Change made while checking the sources

The Data Class rule in `src/java_rules.py` first used NOPA+NOAM > 2. PMD's
implementation of the same Lanza–Marinescu strategy uses > 3, so the rule was
changed to > 3 on 21 October 2026 to match a published implementation. On the
MLCQ test repositories its F1 moved from 0.444 to 0.442.
