# Legacy systems for the next presentation demo

Panel comment: identify a relevant existing or legacy system, explain its limitations clearly, and show a working demo of how RefactorIQ improves on it, with the functions and outputs of each module visible.

So we need one real legacy codebase that our pipeline can actually run on, a short list of its limitations, and a demo that ends with a before/after.

Ready-to-upload ZIPs are in `Research Docs\Testing repos\Legacy demo\`.

## What our system can take

I checked these limits in the code before picking anything:

| Limit | Where | What it means for the demo |
|---|---|---|
| Full pipeline (CUQA → RDP → SCTVA → DIWO) only for Java and Python | SCTVA rejects other languages | Use Java for the main demo. C works in CUQA only. |
| Java parser is `javalang` (Java 8 syntax and older) | CUQA, ML model | Old Java 1.3–1.6 code is fine. |
| Python parser is Python 3 `ast` | CUQA | Python 2 code does not parse, so no Python 2 era projects. |
| Whole-repo quality report reads every source file (50-file cap removed on 20 Oct 2026) | `main.py`, `/api/quality-report` | The full repo can be uploaded. OpenMRS (1,364 files) took about 45–60 s, so allow a minute for big repos. |
| ZIP up to 500 MB, RDP upload 16 MB, SCTVA 5 MB per file | CUQA, RDP, SCTVA | Not a problem for module-sized ZIPs. |
| K&R style C functions are not detected | CUQA C detector | Avoid old zlib as a main demo. |

## Main pick: Apache Log4j 1.2.17 (Java)

Repo: [apache/logging-log4j1](https://github.com/apache/logging-log4j1), tag `v1_2_17` (archived on GitHub).
Demo ZIP: `log4j-1.2.17-core-net.zip` = `org/apache/log4j` (33 files) + `org/apache/log4j/net` (11 files).

Why it is a good "legacy system":

- Apache announced end of life for Log4j 1.x on 5 August 2015. No fixes are released any more.
- It has known high severity issues that will never be fixed in 1.x: CVE-2019-17571 (SocketServer deserialization), CVE-2021-4104 (JMSAppender JNDI), plus CVE-2020-9488 (SMTPAppender), CVE-2022-23302 (JMSSink) and CVE-2022-23305 (JDBCAppender). Several of these classes are in the `net` package we upload.
- A lot of old enterprise systems still carry it, so "how do we clean up and move away from this" is a real question.

What CUQA found when I ran the demo ZIP through the local CUQA server (rules mode, 18 Oct 2026):

| Measure | Value |
|---|---|
| Files / lines of code | 44 / 16,575 |
| Code smells | 100 (39 high, 20 medium, 41 low) |
| Average quality | 90.2 / 100 |
| ML model second opinion | 54 smells |
| Most common smells (core) | Too Many Parameters 20, Magic Number 15, Long Method 14, Large Class 9 |
| Worst files | `LogMF.java` 32/100 (17 smells), `PropertyConfigurator.java` 51, `LogSF.java` 52, `SyslogAppender.java` 71, `SocketHubAppender.java` 74 |

Limitations to say on the slide (code level, from CUQA): long methods and very long parameter lists in the formatting helpers (`LogMF`, `LogSF`), a large configurator class, and network appenders with long methods and magic numbers, all in code nobody maintains any more.

Be clear with the panel: RefactorIQ refactors code smells. It does not patch the CVEs. The CVEs are there to show why this is a risky legacy system, not something we claim to fix.

## Backup Java pick: Apache Struts 1.3.10

Repo: [apache/struts1](https://github.com/apache/struts1), tag `STRUTS_1_3_10` (archived). Same source as `Testing repos\Java\struts1-STRUTS_1_3_10.zip`.
Demo ZIP: `struts-1.3.10-action.zip` = `core/.../org/apache/struts/action` (21 files).

- Last release was December 2008 and Apache says Struts 1 has reached end of life with no more security or bug fixes.
- CVE-2014-0114: ActionForm lets an attacker reach the ClassLoader through the `class` parameter (1.x up to 1.3.10).
- CUQA: 21 files, 6,847 LOC, 24 smells (22 high, mostly Long Method), average quality 91.2, ML second opinion 35. `ActionServlet.java` scores 28/100 with 9 smells and `RequestProcessor.java` 52/100.

## Python pick: Bottle 0.12.25

Repo: [bottlepy/bottle](https://github.com/bottlepy/bottle), tag `0.12.25`. Demo ZIP: `bottle-0.12.25.zip`.

- The whole framework is one 3,809-line module, a classic monolith.
- CUQA: 129 smells (11 high, 20 medium, 98 low), quality 0/100. Top smells: Magic Number 54, Lazy Class 32, Dead Code 9, Long Method 8, Feature Envy 7.
- Honest note: the 0.12 branch still gets small maintenance releases, so call it a legacy-style monolith, not an end-of-life system.

## Warm-up pick (all three languages): Legacy Code Retreat "Trivia"

Repo: [jbrains/trivia](https://github.com/jbrains/trivia), a codebase written on purpose as bad legacy code for refactoring practice. Demo ZIP: `trivia-legacy-retreat.zip` (Java, Python 3 and C versions).

- CUQA: Java 15 smells, Python 18, C 26 (`game.c` 57/100, `player.c` 67/100).
- Good for a quick 1-minute "what each module does" walkthrough, because it is tiny.

## Already in `Testing repos`

- Ant 1.8.4: still a fine Java test (the `taskdefs/email` package gives 7 smells). Ant itself is still maintained, so it is "old version" rather than end of life.
- OpenMRS 1.9.12, curl 7.29, redis 2.8.24: large repos. Since the 50-file cap was removed they can be uploaded whole (OpenMRS: 1,364 files analysed in about a minute), but a package folder is still quicker for a live demo.
- zlib 1.2.8: uses K&R function definitions, which CUQA does not detect, so results look too clean.

## Not good picks (checked)

- Commons Collections 3.2.1 `functors`: has the famous deserialization story, but the 48 small classes gave only 1 smell, so there is nothing to show.
- JUnit 3.8.2 `junit/framework`: only 3 smells.

## Demo flow that shows the improvement

1. Repository Input → upload `log4j-1.2.17-core-net.zip`.
2. CUQA page: go through F1 to F5 (understand, AST, smells with rules + ML, quality scores, JSON report). Point at `LogMF.java` 32/100.
3. DIWO: select smells → RDP plan → approve steps → SCTVA transforms and validates → Results → Comparison.
4. On Comparison, download the project ZIP, then press **Complete Workflow & View Report**. The Reports page now opens the session report for the whole repo.
5. For a measured "after", upload the downloaded project ZIP again in Repository Input and compare the CUQA scores of the refactored files with step 2. Use this rather than the before/after numbers on DIWO's Comparison page, because those are DIWO estimates from smell counts, not measured from code.

Not done yet: I ran steps 3–4 end to end only on the Ant `email` package (SCTVA changed 2 files at 93.6% confidence). Log4j has not gone through SCTVA yet, and the RDP agent was not running locally, so DIWO used its fallback plan. Start RDP for the real demo and do one dry run on Log4j.

## If the panel meant an existing tool, not a legacy codebase

Short version for one slide:

| Existing approach | What it does | Limitation | What RefactorIQ adds |
|---|---|---|---|
| Static analysers (SonarQube, PMD, Checkstyle) | Report smells and rule violations | Stop at detection; no plan, no safe automatic change | Plan per smell (RDP), validated transformation (SCTVA) |
| IDE refactoring (IntelliJ, Eclipse) | Apply one refactoring the developer picks | Manual, one at a time, no priority across the repo | Ranked plan for the whole repo, developer approves in one place (DIWO) |
| Plain rule thresholds | Fixed limits like "method > 30 lines" | Miss context | ML second opinion trained on MLCQ (CUQA) |

## Sources

- [Log4j 1.x end of life and CVE list (Apache)](https://logging.apache.org/log4j/1.x)
- [Struts 1 end of life announcement (Apache)](https://struts.apache.org/struts1eol-announcement.html)
- [CVE-2014-0114 (Red Hat bug)](https://bugzilla.redhat.com/show_bug.cgi?id=1091938)
- [NCC Group on Struts 1 being end of life](https://www.nccgroup.com/research/apache-struts-vulnerability/)
- GitHub repository data from the GitHub API, 18 Oct 2026
