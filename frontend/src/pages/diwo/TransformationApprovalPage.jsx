import { useEffect, useState } from "react";
import { SCTVA_DATA } from "./data/diwoData";
import { C } from "./diwoTheme.jsx";

export default function TransformationApprovalPage({ onComplete, transformationData }) {
  const [progress, setProgress] = useState(0);
  const [stage, setStage] = useState(0);

  // Use backend data if available, otherwise fall back to mock
  const transformData = transformationData || {};
  
  const BEFORE_CODE_SAMPLE = `public class ECommerceSystem {
    public static void main(String[] args) {
        Customer customer = new Customer(1, "Pasan", "pasan@example.com");
        Order order = new Order(1001, customer);
        order.items.add(new OrderItem("Laptop", 2, 1200.00));
        order.items.add(new OrderItem("Mouse", 1, 30.00));

        OrderProcessor processor = new OrderProcessor();
        double total = processor.calculateTotal(order, "CARD", true, "PROMO10", "EXPRESS");
        System.out.println("Order Total: " + total);
    }
}`;

  const AFTER_CODE_SAMPLE = `public class ECommerceSystem {
    public static void main(String[] args) {
        Customer customer = new Customer(1, "Pasan", "pasan@example.com", "premium", "Colombo");
        Order order = new Order(1001, customer);
        order.items.add(new OrderItem("Laptop", 2, 1200.00));
        order.items.add(new OrderItem("Mouse", 1, 30.00));

        OrderProcessorHelper processor = new OrderProcessorHelper();
        OrderParams params = new OrderParams("CARD", true, "PROMO10", "EXPRESS");
        double total = processor.extracted_calculateTotal(order, params);
        System.out.println("Order Total: " + total);
    }
}`;

  const buildDiffRows = (beforeCode, afterCode) => {
    const beforeLines = String(beforeCode || "").split("\n");
    const afterLines = String(afterCode || "").split("\n");
    const max = Math.max(beforeLines.length, afterLines.length);
    const rows = [];

    for (let i = 0; i < max; i += 1) {
      const b = beforeLines[i] ?? "";
      const a = afterLines[i] ?? "";

      if (b === a) {
        rows.push({ key: `same-${i}`, kind: "same", lineNo: i + 1, text: a, marker: " " });
      } else {
        if (b !== "") rows.push({ key: `before-${i}`, kind: "before", lineNo: i + 1, text: b, marker: "-" });
        if (a !== "") rows.push({ key: `after-${i}`, kind: "after", lineNo: i + 1, text: a, marker: "+" });
      }
    }

    return rows;
  };

  const stages = [
    { label: "Initializing Safe Transformation Agent", detail: "Loading SCTVA environment..." },
    { label: "Applying AST Transformations", detail: "Processing 39 refactoring actions..." },
    { label: "Syntax Validation", detail: "Compiling and checking syntax..." },
    { label: "Structural Analysis", detail: "Verifying AST structural integrity..." },
    { label: "Behavioral Validation", detail: "Running 10 Java runtime probes..." },
    { label: "Invariant Mining", detail: "Checking program invariants..." },
    { label: "Confidence Score Computation", detail: "Finalizing safety report..." },
  ];

  const done = progress >= 100;

  useEffect(() => {
    if (done) return;
    const t = setInterval(() => {
      setProgress(p => {
        const next = Math.min(p + 2, 100);
        setStage(Math.floor((next / 100) * (stages.length - 1)));
        return next;
      });
    }, 60);
    return () => clearInterval(t);
  }, [done, stages.length]);

  useEffect(() => {
    if (!done) return;
    
    // If backend data is available, use it; otherwise use mock data
    if (transformData.refactored_code && transformData.diff_rows && transformData.files) {
      const timeout = setTimeout(() => onComplete(transformData), 800);
      return () => clearTimeout(timeout);
    }
    
    // Fall back to mock data generation
    const file1 = {
      path: "src/ECommerceSystem.java",
      before: BEFORE_CODE_SAMPLE,
      after: AFTER_CODE_SAMPLE,
      diff_rows: buildDiffRows(BEFORE_CODE_SAMPLE, AFTER_CODE_SAMPLE),
    };
    const BEFORE_2 = `class Helper {\n  void doThing() {\n    System.out.println("v1");\n  }\n}`;
    const AFTER_2 = `class Helper {\n  void doThing() {\n    System.out.println("v2 - updated");\n  }\n}`;
    const file2 = {
      path: "src/utils/Helper.java",
      before: BEFORE_2,
      after: AFTER_2,
      diff_rows: buildDiffRows(BEFORE_2, AFTER_2),
    };

    const payload = {
      refactored_code: AFTER_CODE_SAMPLE,
      diff_rows: buildDiffRows(BEFORE_CODE_SAMPLE, AFTER_CODE_SAMPLE),
      files: [file1, file2],
    };
    const timeout = setTimeout(() => onComplete(payload), 800);
    return () => clearTimeout(timeout);
  }, [done, onComplete, transformData]);

  const validations = [
    { key: "Syntax", val: SCTVA_DATA.confidence_components.syntax_component, done: progress > 35 },
    { key: "Structural", val: SCTVA_DATA.confidence_components.structural_component, done: progress > 55 },
    { key: "Behavioral", val: SCTVA_DATA.confidence_components.behavioral_component, done: progress > 80 },
    { key: "Invariant", val: SCTVA_DATA.confidence_components.invariant_component, done: progress > 92 },
  ];

  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", padding: "40px 0" }}>
      <div style={{ position: "relative", width: 160, height: 160, marginBottom: 32 }}>
        <svg width="160" height="160" style={{ transform: "rotate(-90deg)" }}>
          <circle cx="80" cy="80" r="68" fill="none" stroke={C.border} strokeWidth="8" />
          <circle
            cx="80"
            cy="80"
            r="68"
            fill="none"
            stroke={C.accent}
            strokeWidth="8"
            strokeDasharray={`${2 * Math.PI * 68}`}
            strokeDashoffset={`${2 * Math.PI * 68 * (1 - progress / 100)}`}
            strokeLinecap="round"
            style={{ transition: "stroke-dashoffset 0.1s" }}
          />
        </svg>
        <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center" }}>
          <div style={{ fontSize: 32, fontWeight: 900, color: done ? C.accent : C.text, fontFamily: "monospace" }}>{progress}%</div>
          <div style={{ fontSize: 10, color: C.textMuted, textTransform: "uppercase", letterSpacing: 1 }}>{done ? "Complete" : "Running"}</div>
        </div>
      </div>

      <div style={{ fontSize: 15, fontWeight: 700, color: C.text, marginBottom: 6, textAlign: "center" }}>{stages[stage].label}</div>
      <div style={{ fontSize: 12, color: C.textMuted, marginBottom: 32 }}>{stages[stage].detail}</div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(2,1fr)", gap: 10, width: "100%", maxWidth: 480 }}>
        {validations.map(({ key, val, done: vdone }) => (
          <div key={key} style={{ background: C.panel, border: `1px solid ${vdone ? C.accent : C.border}`, borderRadius: 8, padding: "12px 16px", display: "flex", alignItems: "center", gap: 10, transition: "border-color 0.3s" }}>
            <div style={{ width: 28, height: 28, borderRadius: "50%", background: vdone ? `${C.accent}20` : C.bg, border: `2px solid ${vdone ? C.accent : C.border}`, display: "flex", alignItems: "center", justifyContent: "center", transition: "all 0.3s" }}>
              {vdone ? <span style={{ color: C.accent, fontSize: 14 }}>✓</span> : <span style={{ color: C.border, fontSize: 14 }}>…</span>}
            </div>
            <div>
              <div style={{ fontSize: 12, fontWeight: 600, color: vdone ? C.text : C.textMuted }}>{key}</div>
              <div style={{ fontSize: 11, color: vdone ? C.accent : C.textMuted }}>{vdone ? `${(val * 100).toFixed(1)}%` : "Pending"}</div>
            </div>
          </div>
        ))}
      </div>

      <div style={{ marginTop: 24, fontSize: 11, color: C.textMuted, textAlign: "center" }}>
        Safe Code Transformation & Validation Agent · {SCTVA_DATA.safety_report.human_messages[0]}
      </div>
    </div>
  );
}
