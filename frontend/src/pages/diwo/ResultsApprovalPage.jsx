import { useState } from "react";
import { SCTVA_DATA } from "./data/diwoData";
import { Badge, C, Card, Pill } from "./diwoTheme.jsx";

export default function ResultsApprovalPage({ onRestart, onRollback, onAccept, refactoredCode, diffRows = [], files = [], repositoryPath = "" }) {
  const [tab, setTab] = useState("summary");
  const [selectedFileIndex, setSelectedFileIndex] = useState(0);
  const [acceptedFiles, setAcceptedFiles] = useState(() => new Set());
  const [showActionChoice, setShowActionChoice] = useState(false);
  const [branchName, setBranchName] = useState("refactoring/diwo-changes");
  const [repoPath, setRepoPath] = useState(repositoryPath || "");
  const [isProcessing, setIsProcessing] = useState(false);
  const confidence = SCTVA_DATA.confidence_score * 100;
  const comps = SCTVA_DATA.confidence_components;
  const beforeRefactorCode = [
    "class Calc:",
    "    def do(self, a, b, type):",
    "        if type == \"add\":",
    "            print(\"Result:\", a + b)",
    "        elif type == \"sub\":",
    "            print(\"Result:\", a - b)",
  ];

  const afterRefactorCode = [
    "class Calculator:",
    "    def display_result(self, result):",
    "        print(\"Result:\", result)",
    "",
    "    def calculate(self, num1, num2, operation):",
    "        operations = {\"add\": num1 + num2, \"sub\": num1 - num2}",
    "        self.display_result(operations.get(operation, \"Invalid operation\"))",
  ];


  const valChecks = [
    { label: "Syntax Validation", status: "passed", detail: "Compilation successful. No errors.", score: comps.syntax_component },
    { label: "Structural Analysis", status: "passed", detail: "AST structural integrity maintained.", score: comps.structural_component },
    { label: "Behavioral Probes", status: "passed", detail: "10 Java runtime probes executed. All matched.", score: comps.behavioral_component },
    { label: "Invariant Mining", status: "passed", detail: "All program invariants preserved.", score: comps.invariant_component },
  ];

  const metrics = [
    { label: "Methods Extracted", before: 0, after: 8, unit: "methods", positive: true },
    { label: "Classes Extracted", before: 0, after: 3, unit: "classes", positive: true },
    { label: "Avg Cyclomatic Complexity", before: 35, after: 12, unit: "", positive: true },
    { label: "Longest Method (LOC)", before: 220, after: 45, unit: "lines", positive: true },
    { label: "Total Refactoring Actions", before: 0, after: 39, unit: "applied", positive: true },
    { label: "Confidence Score", before: "-", after: "100%", unit: "", positive: true },
  ];

  const fileEntries = (files && files.length > 0)
    ? files
    : refactoredCode
      ? [{ path: `refactored_code.${(SCTVA_DATA.language || "txt").toLowerCase()}`, after: refactoredCode }]
      : [];

  const getFileKey = (fileEntry, idx) => fileEntry.path || fileEntry.file || `file-${idx}`;

  const acceptedFileEntries = fileEntries.filter((fileEntry, idx) => acceptedFiles.has(getFileKey(fileEntry, idx)));

  const toggleAcceptedFile = (fileEntry, idx) => {
    const fileKey = getFileKey(fileEntry, idx);
    setAcceptedFiles((prev) => {
      const next = new Set(prev);
      if (next.has(fileKey)) next.delete(fileKey);
      else next.add(fileKey);
      return next;
    });
  };

  // Save text content to a local file via browser download
  const saveTextFile = (filename, content) => {
    try {
      const blob = new Blob([content], { type: "text/plain;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1500);
    } catch (e) {
      // graceful fallback: log
      // eslint-disable-next-line no-console
      console.error("Failed to save file", e);
    }
  };

  const handleAcceptAndDownload = () => {
    const toSave = acceptedFileEntries;

    if (toSave.length === 0) {
      alert("Select at least one accepted file before downloading.");
    } else {
      toSave.forEach((f, i) => {
        const filename = f.path ? f.path.split("/").pop() : `refactored_${i + 1}.txt`;
        const content = f.after || f.refactored_code || refactoredCode || "";
        saveTextFile(filename, content);
      });

      // Save a small metadata file for convenience
      const meta = {
        savedAt: new Date().toISOString(),
        count: toSave.length,
        files: toSave.map((f) => f.path || null),
      };
      saveTextFile("diwo_refactored_metadata.json", JSON.stringify(meta, null, 2));
    }

    setShowActionChoice(false);
    if (onAccept) onAccept({ acceptedFiles: toSave });
  };

  const handlePushToGitHub = async () => {
    if (!branchName.trim()) {
      alert("Please enter a branch name");
      return;
    }

    if (!repoPath || repoPath.trim() === "") {
      alert("Repository path is required. Please provide the path to your Git repository.");
      return;
    }

    setIsProcessing(true);
    try {
      if (acceptedFileEntries.length === 0) {
        alert("Select at least one accepted file before sharing to GitHub Desktop.");
        return;
      }

      const payload = {
        files: acceptedFileEntries,
        branch_name: branchName,
        repository_path: repoPath.trim(),
      };

      const response = await fetch("/api/diwo/apply-and-push", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (!response.ok) {
        let errorMessage = `Backend error: ${response.statusText}`;
        try {
          const errorData = await response.json();
          errorMessage = errorData.error || errorMessage;
        } catch {
          // Response is not JSON (e.g., 404 HTML), use status message
          errorMessage = `HTTP ${response.status}: ${response.statusText}`;
        }
        throw new Error(errorMessage);
      }

      const result = await response.json();
      const staged = result.staged_files || [];
      const repoOpened = result.github_desktop_opened ? '\n\nGitHub Desktop should be opening now.' : '';
      let message = `✓ Branch '${branchName}' created and files applied to repository:\n${result.repository}${repoOpened}\n\n`;
      if (staged.length > 0) {
        const stagedList = staged.map(s => ' - ' + s).join('\n');
        message += `Staged files (${staged.length}):\n` + stagedList + '\n\nPlease review and commit in GitHub Desktop.';
      } else {
        message += 'No staged files were detected. Check repository path and file paths.';
      }
      alert(message);
      
      setShowActionChoice(false);
      if (onAccept) onAccept({ acceptedFiles: acceptedFileEntries, githubResult: result });
    } catch (error) {
      // eslint-disable-next-line no-console
      console.error("Failed to push to GitHub:", error);
      alert(`Error: ${error.message}`);
    } finally {
      setIsProcessing(false);
    }
  };

  return (
    <div>
      <Card glow={C.accentGlow} style={{ marginBottom: 20, textAlign: "center", padding: "28px" }}>
        <div style={{ fontSize: 56, fontWeight: 900, fontFamily: "monospace", background: C.gradient, WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent" }}>
          {confidence.toFixed(0)}%
        </div>
        <div style={{ fontSize: 14, color: C.textSub, marginTop: 4, marginBottom: 12 }}>Transformation Confidence Score</div>
        <div style={{ display: "flex", justifyContent: "center", gap: 12, flexWrap: "wrap" }}>
          <Pill label="✓ Transformation Successful" color={C.accent} />
          <Pill label="✓ No Rollback Required" color={C.accent} />
          <Pill label="✓ All Validations Passed" color={C.accent} />
        </div>
        <div style={{ marginTop: 12, fontSize: 12, color: C.textMuted }}>{SCTVA_DATA.safety_report.summary}</div>
      </Card>

      <div style={{ display: "flex", gap: 4, marginBottom: 16, borderBottom: `1px solid ${C.border}`, paddingBottom: 0 }}>
        {[["summary", "Summary"], ["validation", "Validation Report"], ["code", "Refactored Code"], ["invariants", "Invariants"]].map(([key, label]) => (
          <button key={key} onClick={() => setTab(key)} style={{
            padding: "8px 16px", borderRadius: "8px 8px 0 0", fontSize: 12, fontWeight: 700, cursor: "pointer", border: "none",
            background: tab === key ? C.accent : "transparent", color: tab === key ? "#000" : C.textMuted,
            borderBottom: tab === key ? "none" : "none"
          }}>{label}</button>
        ))}
      </div>

      {tab === "summary" && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(2,1fr)", gap: 10 }}>
          {metrics.map(({ label, before, after, unit, positive }) => (
            <div key={label} style={{ background: C.panel, border: `1px solid ${C.border}`, borderRadius: 8, padding: "14px 16px" }}>
              <div style={{ fontSize: 11, color: C.textMuted, textTransform: "uppercase", letterSpacing: 0.8, marginBottom: 8 }}>{label}</div>
              <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                {before !== "-" && before !== 0 && <div style={{ fontSize: 18, color: C.textMuted, fontFamily: "monospace", textDecoration: "line-through" }}>{before}{unit ? ` ${unit}` : ""}</div>}
                {before !== "-" && before !== 0 && <span style={{ color: C.textMuted, fontSize: 12 }}>→</span>}
                <div style={{ fontSize: 22, fontWeight: 800, color: positive ? C.accent : C.danger, fontFamily: "monospace" }}>{after}{unit ? ` ${unit}` : ""}</div>
              </div>
            </div>
          ))}
          <div style={{ background: C.panel, border: `1px solid ${C.border}`, borderRadius: 8, padding: "14px 16px", gridColumn: "span 2" }}>
            <div style={{ fontSize: 11, color: C.textMuted, textTransform: "uppercase", letterSpacing: 0.8, marginBottom: 10 }}>Confidence Formula</div>
            <div style={{ fontSize: 11, color: C.textSub, fontFamily: "monospace", marginBottom: 10 }}>{comps.formula}</div>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              {[["Syntax (35%)", comps.syntax_component], ["Structural (35%)", comps.structural_component], ["Behavioral (30%)", comps.behavioral_component], ["Invariant (15%)", comps.invariant_component]].map(([lbl, val]) => (
                <div key={lbl} style={{ background: C.bg, borderRadius: 6, padding: "8px 12px", textAlign: "center" }}>
                  <div style={{ fontSize: 16, fontWeight: 800, color: C.accent, fontFamily: "monospace" }}>{(val * 100).toFixed(2)}%</div>
                  <div style={{ fontSize: 10, color: C.textMuted }}>{lbl}</div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {tab === "validation" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {valChecks.map(({ label, detail, score }) => (
            <div key={label} style={{ background: C.panel, border: `1px solid ${C.accent}30`, borderRadius: 8, padding: "14px 18px", display: "flex", alignItems: "center", gap: 16 }}>
              <div style={{ width: 36, height: 36, borderRadius: "50%", background: `${C.accent}20`, border: `2px solid ${C.accent}`, display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
                <span style={{ color: C.accent, fontWeight: 900 }}>✓</span>
              </div>
              <div style={{ flex: 1 }}>
                <div style={{ fontWeight: 700, color: C.text, fontSize: 13 }}>{label}</div>
                <div style={{ fontSize: 12, color: C.textSub, marginTop: 2 }}>{detail}</div>
              </div>
              <div style={{ textAlign: "right" }}>
                <div style={{ fontSize: 18, fontWeight: 800, color: C.accent, fontFamily: "monospace" }}>{(score * 100).toFixed(2)}%</div>
                <Pill label="PASSED" color={C.accent} />
              </div>
            </div>
          ))}
          <Card style={{ marginTop: 4 }}>
            <div style={{ fontSize: 12, color: C.textMuted, marginBottom: 8, fontWeight: 700, textTransform: "uppercase", letterSpacing: 1 }}>Safety Messages</div>
            {SCTVA_DATA.safety_report.human_messages.map((msg, i) => (
              <div key={i} style={{ fontSize: 12, color: C.textSub, padding: "4px 0", borderBottom: i < SCTVA_DATA.safety_report.human_messages.length - 1 ? `1px solid ${C.border}` : "none" }}>
                <span style={{ color: C.accent, marginRight: 8 }}>→</span>{msg}
              </div>
            ))}
          </Card>
        </div>
      )}

      {tab === "code" && (
        <div style={{ display: "flex", gap: 12 }}>
          <div style={{ width: 260, maxHeight: 360, overflow: "auto", background: C.panel, border: `1px solid ${C.border}`, borderRadius: 8, padding: 8 }}>
            <div style={{ fontSize: 12, color: C.textMuted, marginBottom: 8 }}>Files</div>
            {fileEntries.length > 0 ? (
              fileEntries.map((f, idx) => {
                const fileKey = getFileKey(f, idx);
                const isAccepted = acceptedFiles.has(fileKey);
                return (
                <div key={fileKey} style={{ padding: 10, borderRadius: 6, cursor: "pointer", background: selectedFileIndex === idx ? C.accent : "transparent", color: selectedFileIndex === idx ? "#000" : C.text, marginBottom: 6, border: `1px solid ${isAccepted ? C.accent : C.border}` }}>
                  <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "start" }}>
                    <div onClick={() => setSelectedFileIndex(idx)} style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 13, fontWeight: 700 }}>{(f.path || f.file || `file-${idx}`).split('/').pop()}</div>
                      <div style={{ fontSize: 11, color: C.textMuted, marginTop: 4, wordBreak: "break-all" }}>{f.path || f.file || `file-${idx}`}</div>
                    </div>
                    <button
                      onClick={(e) => { e.stopPropagation(); toggleAcceptedFile(f, idx); }}
                      style={{
                        padding: "5px 10px",
                        borderRadius: 6,
                        cursor: "pointer",
                        border: `1px solid ${isAccepted ? C.accent : C.border}`,
                        background: isAccepted ? C.accent : C.bg,
                        color: isAccepted ? "#000" : C.textMuted,
                        fontSize: 11,
                        fontWeight: 700,
                      }}
                    >
                      {isAccepted ? "Accepted" : "Accept"}
                    </button>
                  </div>
                </div>
                );
              })
            ) : (
              <div style={{ color: C.textMuted }}>No file-level diffs available.</div>
            )}
          </div>

          <div style={{ flex: 1 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
              <div style={{ fontSize: 12, color: C.textMuted }}>{fileEntries[selectedFileIndex] ? (fileEntries[selectedFileIndex].path || fileEntries[selectedFileIndex].file) : `ECommerceSystem.java · ${SCTVA_DATA.language.toUpperCase()}`}</div>
              <Badge label="Refactored Diff" color={C.accent} />
            </div>

            <div style={{ marginTop: 8 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8, fontSize: 11 }}>
                <span style={{ color: "#ef4444", fontWeight: 700 }}>- Before / Code Smell</span>
                <span style={{ color: "#3b82f6", fontWeight: 700 }}>+ After / Refactored</span>
              </div>

              <div style={{
                background: "#0b1020",
                border: `1px solid ${C.border}`,
                borderRadius: 10,
                overflow: "auto",
                marginBottom: 12,
                fontFamily: "Fira Code, Courier New, monospace",
                fontSize: 11,
                lineHeight: 1.55,
              }}>
                {[
                  ...beforeRefactorCode.map((text, index) => ({ key: `before-${index}`, kind: "before", marker: "-", lineNo: index + 1, text })),
                  ...afterRefactorCode.map((text, index) => ({ key: `after-${index}`, kind: "after", marker: "+", lineNo: index + 1, text })),
                ].map((row) => {
                  const isBefore = row.kind === "before";
                  return (
                    <div
                      key={row.key}
                      style={{
                        display: "grid",
                        gridTemplateColumns: "56px 24px 1fr",
                        padding: "2px 0",
                        background: isBefore ? "rgba(239,68,68,0.12)" : "rgba(59,130,246,0.12)",
                        borderBottom: "1px solid rgba(148,163,184,0.06)",
                      }}
                    >
                      <span style={{ color: "#64748b", textAlign: "right", paddingRight: 8, userSelect: "none" }}>{row.lineNo}</span>
                      <span style={{ color: isBefore ? "#ef4444" : "#3b82f6", textAlign: "center", userSelect: "none", fontWeight: 700 }}>{row.marker}</span>
                      <span style={{ whiteSpace: "pre", color: isBefore ? "#fecaca" : "#bfdbfe", padding: "0 10px 0 6px" }}>{row.text || " "}</span>
                    </div>
                  );
                })}
              </div>

              <div style={{
                background: "#0b1020",
                border: `1px solid ${C.border}`,
                borderRadius: 10,
                overflow: "auto",
                maxHeight: 360,
                fontFamily: "Fira Code, Courier New, monospace",
                fontSize: 11,
                lineHeight: 1.55,
              }}>
                {/* {((fileEntries[selectedFileIndex]) ? (fileEntries[selectedFileIndex].diff_rows || []) : (diffRows || [])).map((row) => {
                  const isBefore = row.kind === "before";
                  const isAfter = row.kind === "after";
                  return (
                    <div
                      key={row.key}
                      style={{
                        display: "grid",
                        gridTemplateColumns: "56px 24px 1fr",
                        gap: 0,
                        padding: "2px 0",
                        background: isBefore
                          ? "rgba(239,68,68,0.12)"
                          : isAfter
                            ? "rgba(59,130,246,0.12)"
                            : "transparent",
                        borderBottom: "1px solid rgba(148,163,184,0.06)",
                      }}
                    >
                      <span style={{ color: "#64748b", textAlign: "right", paddingRight: 8, userSelect: "none" }}>{row.lineNo}</span>
                      <span style={{
                        color: isBefore ? "#ef4444" : isAfter ? "#3b82f6" : "#94a3b8",
                        textAlign: "center",
                        userSelect: "none",
                        fontWeight: 700,
                      }}>{row.marker}</span>
                      <span style={{
                        whiteSpace: "pre",
                        color: isBefore ? "#fecaca" : isAfter ? "#bfdbfe" : "#cbd5e1",
                        padding: "0 10px 0 6px",
                      }}>{row.text || " "}</span>
                    </div>
                  );
                })} */}
              </div>
            </div>
          </div>
        </div>
      )}

      {tab === "invariants" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={{ fontSize: 12, color: C.textSub, marginBottom: 4 }}>Preserved program invariants verified post-transformation:</div>
          {["execution_success_consistency", "return_type_consistency", "non_null_return_consistency", "exception_pattern_consistency"].map(inv => (
            <div key={inv} style={{ background: C.panel, border: `1px solid ${C.accent}20`, borderRadius: 8, padding: "12px 16px", display: "flex", alignItems: "center", gap: 10 }}>
              <span style={{ color: C.accent }}>✓</span>
              <span style={{ fontSize: 12, fontFamily: "monospace", color: C.textSub }}>{inv}</span>
              <Pill label="PRESERVED" color={C.accent} />
            </div>
          ))}
        </div>
      )}

      <div style={{ display: "flex", gap: 12, marginTop: 24, justifyContent: "space-between", alignItems: "center", flexWrap: "wrap" }}>
        <div style={{ fontSize: 12, color: C.textSub }}>
          Accepted files: <span style={{ color: C.accent, fontWeight: 700 }}>{acceptedFileEntries.length}</span> / {fileEntries.length}
        </div>
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap", justifyContent: "flex-end" }}>
        <button onClick={onRollback} style={{ padding: "10px 20px", borderRadius: 8, fontWeight: 700, fontSize: 13, cursor: "pointer", background: `${C.danger}15`, color: C.danger, border: `1px solid ${C.danger}30` }}>
          ↩ Request Rollback
        </button>
        <button onClick={onRestart} style={{ padding: "10px 20px", borderRadius: 8, fontWeight: 700, fontSize: 13, cursor: "pointer", background: `${C.info}15`, color: C.info, border: `1px solid ${C.info}30` }}>
          ↺ New Refactoring Session
        </button>
        <button onClick={() => setShowActionChoice(true)} style={{ padding: "10px 24px", borderRadius: 8, fontWeight: 700, fontSize: 13, cursor: "pointer", background: C.accent, color: "#000", border: "none", boxShadow: `0 0 20px ${C.accentGlow}` }}>
          ✓ Accept & Commit Changes
        </button>
        </div>
      </div>

      {showActionChoice && (
        <div style={{
          position: "fixed",
          top: 0,
          left: 0,
          right: 0,
          bottom: 0,
          background: "rgba(0,0,0,0.5)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          zIndex: 1000,
        }}>
          <div style={{
            background: C.bg,
            border: `1px solid ${C.border}`,
            borderRadius: 12,
            padding: 24,
            maxWidth: 480,
            boxShadow: "0 20px 60px rgba(0,0,0,0.3)",
          }}>
            <h2 style={{ marginTop: 0, color: C.text, marginBottom: 12 }}>How would you like to proceed?</h2>
            <p style={{ color: C.textSub, marginBottom: 20 }}>Choose how to save and manage your approved refactored code.</p>

            <div style={{ display: "flex", flexDirection: "column", gap: 12, marginBottom: 16 }}>
              {/* Download Option */}
              <div onClick={() => handleAcceptAndDownload()} style={{
                background: C.panel,
                border: `2px solid ${C.border}`,
                borderRadius: 8,
                padding: 16,
                cursor: "pointer",
                transition: "all 0.2s",
              }} onMouseEnter={(e) => e.currentTarget.style.borderColor = C.accent} onMouseLeave={(e) => e.currentTarget.style.borderColor = C.border}>
                <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                  <div style={{ fontSize: 24 }}>💾</div>
                  <div style={{ flex: 1 }}>
                    <div style={{ fontWeight: 700, color: C.text, marginBottom: 4 }}>Download to Local Device</div>
                    <div style={{ fontSize: 12, color: C.textMuted }}>Save refactored files to your downloads folder. You manage the repository yourself.</div>
                  </div>
                </div>
              </div>

              {/* GitHub Desktop Option */}
              <div style={{
                background: C.panel,
                border: `2px solid ${C.border}`,
                borderRadius: 8,
                padding: 16,
              }}>
                <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 12 }}>
                  <div style={{ fontSize: 24 }}>🚀</div>
                  <div style={{ flex: 1 }}>
                    <div style={{ fontWeight: 700, color: C.text }}>Push to GitHub</div>
                    <div style={{ fontSize: 12, color: C.textMuted }}>Create a branch and open GitHub Desktop for review & commit.</div>
                  </div>
                </div>
                <div style={{ marginBottom: 10 }}>
                  <label style={{ fontSize: 12, color: C.textMuted, display: "block", marginBottom: 4 }}>Repository Path</label>
                  <input
                    type="text"
                    value={repoPath}
                    onChange={(e) => setRepoPath(e.target.value)}
                    placeholder="C:\\Users\\YourUser\\path\\to\\repo"
                    style={{
                      width: "100%",
                      padding: "8px 12px",
                      borderRadius: 6,
                      background: C.bg,
                      border: `1px solid ${C.border}`,
                      color: C.text,
                      fontSize: 12,
                      marginBottom: 10,
                      boxSizing: "border-box",
                    }}
                  />
                </div>
                <div style={{ marginBottom: 10 }}>
                  <label style={{ fontSize: 12, color: C.textMuted, display: "block", marginBottom: 4 }}>Branch Name</label>
                  <input
                    type="text"
                    value={branchName}
                    onChange={(e) => setBranchName(e.target.value)}
                    placeholder="e.g., refactoring/diwo-changes"
                    style={{
                      width: "100%",
                      padding: "8px 12px",
                      borderRadius: 6,
                      background: C.bg,
                      border: `1px solid ${C.border}`,
                      color: C.text,
                      fontSize: 12,
                      marginBottom: 10,
                      boxSizing: "border-box",
                    }}
                  />
                </div>
                <button
                  onClick={handlePushToGitHub}
                  disabled={isProcessing}
                  style={{
                    width: "100%",
                    padding: "8px 12px",
                    borderRadius: 6,
                    background: C.accent,
                    color: "#000",
                    border: "none",
                    fontWeight: 700,
                    fontSize: 12,
                    cursor: isProcessing ? "not-allowed" : "pointer",
                    opacity: isProcessing ? 0.6 : 1,
                  }}
                >
                  {isProcessing ? "Processing..." : "✓ Create Branch & Open GitHub Desktop"}
                </button>
              </div>

              {/* Cancel Button */}
              <button
                onClick={() => setShowActionChoice(false)}
                style={{
                  width: "100%",
                  padding: "8px 12px",
                  borderRadius: 6,
                  background: C.border,
                  color: C.text,
                  border: "none",
                  fontWeight: 700,
                  fontSize: 12,
                  cursor: "pointer",
                }}
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
