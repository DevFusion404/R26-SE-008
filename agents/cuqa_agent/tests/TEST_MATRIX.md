# CUQA Agent Test Matrix

| ID | Category | Component | Scenario | Input | Expected Result | Language | Priority | Automated? | Test File | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| CUQA-LANG-001 | Unit | detect_language | Detect `.py` extension | `main.py` | `"python"` | Python | High | Yes | `unit/test_language_detection.py` | PASS |
| CUQA-LANG-002 | Unit | detect_language | Detect `.java` extension | `Main.java` | `"java"` | Java | High | Yes | `unit/test_language_detection.py` | PASS |
| CUQA-LANG-003 | Unit | detect_language | Detect `.c` extension | `main.c` | `"c"` | C | High | Yes | `unit/test_language_detection.py` | PASS |
| CUQA-LANG-004 | Unit | detect_language | Detect `.h` extension | `utils.h` | `"c"` | C | High | Yes | `unit/test_language_detection.py` | PASS |
| CUQA-LANG-005 | Unit | detect_language | Case insensitivity | `MAIN.PY` | `"python"` | Python | Medium | Yes | `unit/test_language_detection.py` | PASS |
| CUQA-LANG-006 | Unit | detect_language | Unsupported file type | `file.js` | `"unknown"` | Other | Medium | Yes | `unit/test_language_detection.py` | PASS |
| CUQA-DISC-001 | Unit | _find_source_files | Discover polyglot repo files | Mixed dir tree | List of 4 relative paths | Mixed | High | Yes | `unit/test_file_discovery.py` | PASS |
| CUQA-DISC-002 | Unit | _find_source_files | Skip ignored directories | `.git`, `node_modules` | Exclude ignored dirs | All | High | Yes | `unit/test_file_discovery.py` | PASS |
| CUQA-DISC-003 | Unit | _get_language_breakdown | Compute breakdown dict | Polyglot file list | Primary lang & is_polyglot=True | Mixed | High | Yes | `unit/test_file_discovery.py` | PASS |
| CUQA-PY-001 | Unit | python_ast_parser | Parse valid Python features | Async, decorators, classes | Standard AST schema | Python | High | Yes | `unit/test_python_parser.py` | PASS |
| CUQA-PY-002 | Unit | python_ast_parser | Malformed Python error | `def broken(` | Structured SyntaxError | Python | High | Yes | `unit/test_python_parser.py` | PASS |
| CUQA-JAVA-001 | Unit | java_ast_parser | Parse valid Java code | Classes, methods, generics | CompilationUnit AST | Java | High | Yes | `unit/test_java_parser.py` | PASS |
| CUQA-JAVA-002 | Unit | java_ast_parser | Malformed Java error | `public class X {` | Structured JavaSyntaxError | Java | High | Yes | `unit/test_java_parser.py` | PASS |
| CUQA-C-001 | Unit | c_ast_parser | Parse C source code | Structs, pointers, macros | TranslationUnit AST | C | High | Yes | `unit/test_c_parser.py` | PASS |
| CUQA-C-002 | Unit | c_ast_parser | Tree-sitter fallback mode | Tree-sitter disabled | Regex-fallback parser | C | High | Yes | `unit/test_c_parser.py` | PASS |
| CUQA-SMELL-001 | Unit | report_generator | LongMethod detection (>30 lines) | 31 body lines | Smell reported with metadata | Python | High | Yes | `unit/test_python_smells.py` | PASS |
| CUQA-SMELL-002 | Unit | report_generator | TooManyParameters (>5 params) | 6 real parameters | Smell reported | Python | High | Yes | `unit/test_python_smells.py` | PASS |
| CUQA-SMELL-003 | Unit | report_generator | SwitchStatements (>=4 elif) | 4 elif branches | Smell reported | Python | Medium | Yes | `unit/test_python_smells.py` | PASS |
| CUQA-SMELL-004 | Unit | report_generator | MessageChains (depth >= 3) | `a.b.c` chain | Smell reported | Python | Low | Yes | `unit/test_python_smells.py` | PASS |
| CUQA-SMELL-005 | Unit | report_generator | LargeClass (>15 methods) | 16 methods | Smell reported | Python | High | Yes | `unit/test_python_smells.py` | PASS |
| CUQA-SMELL-006 | Unit | report_generator | MagicNumber detection | `val = 999` | Smell reported | Python | Low | Yes | `unit/test_python_smells.py` | PASS |
| CUQA-SMELL-007 | Unit | report_generator | BareExcept detection | `except:` | Smell reported | Python | Medium | Yes | `unit/test_python_smells.py` | PASS |
| CUQA-SMELL-008 | Unit | report_generator | DeadCode detection | Unused function | Smell reported | Python | Low | Yes | `unit/test_python_smells.py` | PASS |
| CUQA-SMELL-009 | Unit | report_generator | DuplicateCode detection | Identical bodies | Smell reported | Python | Medium | Yes | `unit/test_python_smells.py` | PASS |
| CUQA-SMELL-010 | Unit | report_generator | Java LongMethod (>30 lines) | 31 LOC Java method | Smell reported | Java | High | Yes | `unit/test_java_smells.py` | PASS |
| CUQA-SMELL-011 | Unit | report_generator | C LongFunction (>40 lines) | 41 LOC C function | Smell reported | C | High | Yes | `unit/test_c_smells.py` | PASS |
| CUQA-SMELL-012 | Unit | report_generator | C DeepNesting (>4 depth) | 5 brace levels | Smell reported | C | High | Yes | `unit/test_c_smells.py` | PASS |
| CUQA-SMELL-013 | Unit | report_generator | C UnsafeFunctionUsage | `strcpy`, `gets` | Smell reported | C | High | Yes | `unit/test_c_smells.py` | PASS |
| CUQA-SMELL-014 | Unit | report_generator | C GlobalVariable | `int counter;` | Smell reported | C | Medium | Yes | `unit/test_c_smells.py` | PASS |
| CUQA-SMELL-015 | Unit | report_generator | C LargeHeaderFile (>300 lines) | 301 line header | Smell reported | C | Medium | Yes | `unit/test_c_smells.py` | PASS |
| CUQA-METRIC-001 | Unit | report_generator | Python metrics calculation | Source code string | LOC, blank, comments, coupling | Python | High | Yes | `unit/test_python_metrics.py` | PASS |
| CUQA-METRIC-002 | Unit | report_generator | Java metrics calculation | Java source string | LOC, functions, classes | Java | High | Yes | `unit/test_java_metrics.py` | PASS |
| CUQA-METRIC-003 | Unit | report_generator | C metrics calculation | C source string | LOC, includes, CC estimate | C | High | Yes | `unit/test_c_metrics.py` | PASS |
| CUQA-SCORE-001 | Unit | report_generator | Quality score calculation | List of smells | Score 0..100 | All | High | Yes | `unit/test_quality_score.py` | PASS |
| CUQA-API-001 | API | GET / | Service root check | Request to `/` | 200 OK agent metadata | All | Low | Yes | `api/test_root_health_api.py` | PASS |
| CUQA-API-002 | API | GET /api/health | Workspace health status | Request to `/api/health` | 200 OK workspace status | All | Medium | Yes | `api/test_root_health_api.py` | PASS |
| CUQA-API-003 | API | POST /api/upload-zip | Upload valid ZIP archive | Polyglot ZIP | 200 OK extraction summary | Mixed | High | Yes | `api/test_upload_zip_api.py` | PASS |
| CUQA-API-004 | API | POST /api/github-repo | Mock GitHub repo download | GitHub URL | 200 OK repo extracted | All | High | Yes | `api/test_github_repo_api.py` | PASS |
| CUQA-API-005 | API | GET /api/files | List workspace files | Loaded workspace | List of files & total count | All | High | Yes | `api/test_files_api.py` | PASS |
| CUQA-API-006 | API | GET /api/project-structure | File tree construction | Loaded workspace | Recursive directory tree | All | High | Yes | `api/test_project_structure_api.py` | PASS |
| CUQA-API-007 | API | POST /api/parse-ast | Single file AST parse | Relative file path | AST JSON and summary | All | High | Yes | `api/test_parse_ast_api.py` | PASS |
| CUQA-API-008 | API | POST /api/quality-report | Quality report generation | Single file / whole repo | File or repo report JSON | All | High | Yes | `api/test_quality_report_api.py` | PASS |
| CUQA-INT-001 | Integration | Workflow | End-to-end Python flow | ZIP upload to report | Full pipeline success | Python | High | Yes | `integration/test_python_repository_flow.py` | PASS |
| CUQA-INT-002 | Integration | Workflow | End-to-end Java flow | ZIP upload to report | Full pipeline success | Java | High | Yes | `integration/test_java_repository_flow.py` | PASS |
| CUQA-INT-003 | Integration | Workflow | End-to-end C flow | ZIP upload to report | Full pipeline success | C | High | Yes | `integration/test_c_repository_flow.py` | PASS |
| CUQA-INT-004 | Integration | Workflow | End-to-end Polyglot flow | ZIP upload to report | Full pipeline success | Mixed | High | Yes | `integration/test_polyglot_repository_flow.py` | PASS |
| CUQA-INT-005 | Integration | CUQA->RDP | Contract translation test | CUQA quality report JSON | Successfully translated by RDP | Mixed | Critical | Yes | `integration/test_cuqa_rdp_contract.py` | PASS |
| CUQA-SEC-001 | Security | Zip Slip | Malicious path in ZIP | `../../evil.py` entry | 400 Bad Request rejection | All | Critical | Yes | `security/test_zip_security.py` | PASS |
| CUQA-SEC-002 | Security | Path Traversal | Traversal path in API | `../../etc/passwd` | 400 Bad Request rejection | All | Critical | Yes | `security/test_path_traversal.py` | PASS |
| CUQA-SEC-003 | Security | GitHub URL Validation | Spoofed GitHub domain | `github.com.evil.com` | 400 Bad Request rejection | All | High | Yes | `security/test_github_url_validation.py` | PASS |
| CUQA-SEC-004 | Security | Malicious Input | Malformed JSON payload | Broken JSON string | 422 Unprocessable Entity | All | Medium | Yes | `security/test_malicious_input.py` | PASS |
| CUQA-EDGE-001 | Edge Case | Empty Files | Zero-byte source files | Empty source strings | Valid empty report, 100 score | All | Medium | Yes | `edge_cases/test_empty_files.py` | PASS |
| CUQA-EDGE-002 | Edge Case | Encodings | UTF-8 BOM, Unicode, Emoji | Sinhala text & invalid bytes | Handled without crashing | All | Medium | Yes | `edge_cases/test_encoding_cases.py` | PASS |
| CUQA-EDGE-003 | Edge Case | Large Files | 1,000+ LOC source files | Synthetic large files | Processed within bounds | All | Medium | Yes | `edge_cases/test_large_source_files.py` | PASS |
| CUQA-EDGE-004 | Edge Case | Boundaries | Exact threshold boundary | 30 vs 31, 40 vs 41 LOC | Correct boundary behavior | All | High | Yes | `edge_cases/test_boundary_values.py` | PASS |
| CUQA-EDGE-005 | Edge Case | Parser Failures | 49 valid + 1 malformed | Polyglot repo with error | 49 files processed cleanly | All | High | Yes | `edge_cases/test_parser_failures.py` | PASS |
| CUQA-REG-001 | Regression | Security Bugs | All known fixed bugs | Regression test cases | All pass permanently | All | Critical | Yes | `regression/test_known_regressions.py` | PASS |
| CUQA-PERF-001 | Performance | Scaling | 100 source file repository | Synthetic 100 file repo | Benchmark logged <10s | Python | Medium | Yes | `performance/test_repository_scaling.py` | PASS |
