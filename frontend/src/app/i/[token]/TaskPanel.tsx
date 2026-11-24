"use client";

import { useEffect, useRef, useState } from "react";
import { api, errorText, mmss, type Task } from "@/lib/api";

type SampleResult = { stdin: string; expected: string; actual: string; passed: boolean; error: string | null };
type SqlRun = { ok: boolean; error?: string; columns?: string[]; rows?: unknown[][]; matches_expected?: boolean };

const cell = (v: unknown) => (v === null ? "NULL" : String(v));

export default function TaskPanel({ base, task, onDone }: { base: string; task: Task; onDone: () => void }) {
  const isSql = task.kind === "sql";
  const langs = task.languages.length ? task.languages : ["python"];
  const starter = (l: string) => task.problem.starter_code?.[l] ?? "";
  const initialLang = task.draft && langs.includes(task.draft.language) ? task.draft.language : langs[0];
  const [lang, setLang] = useState(initialLang);
  const [code, setCode] = useState<Record<string, string>>(() => {
    const buf: Record<string, string> = {};
    for (const l of langs) buf[l] = starter(l);
    if (task.draft) buf[initialLang] = task.draft.code;
    return buf;
  });
  const [left, setLeft] = useState(task.remaining_s);
  const [busy, setBusy] = useState<"" | "run" | "submit" | "skip">("");
  const [err, setErr] = useState("");
  const [samples, setSamples] = useState<SampleResult[] | null>(null);
  const [sql, setSql] = useState<SqlRun | null>(null);
  const saved = useRef(code[initialLang]);
  const current = isSql ? code.sql ?? "" : code[lang] ?? "";

  useEffect(() => { setLeft(task.remaining_s); }, [task.remaining_s]);
  useEffect(() => {
    const t = setInterval(() => setLeft((s) => {
      if (s === 1) setTimeout(onDone, 4500); // the server auto-submits the saved draft a few seconds after zero
      return Math.max(0, s - 1);
    }), 1000);
    return () => clearInterval(t);
  }, [onDone]);

  const body = () => ({ language: isSql ? "python" : lang, code: current });

  useEffect(() => {  // autosave the draft every few seconds while it changes
    const t = setInterval(() => {
      if (current !== saved.current) {
        saved.current = current;
        api(`${base}/task/save`, { json: body() }).catch(() => {});
      }
    }, 3000);
    return () => clearInterval(t);
  }); // eslint-disable-line react-hooks/exhaustive-deps

  async function run() {
    setBusy("run"); setErr(""); setSamples(null); setSql(null);
    try {
      const r = await api<{ results?: SampleResult[] } & SqlRun>(`${base}/task/run`, { json: body() });
      if (isSql) setSql(r); else setSamples(r.results ?? []);
    } catch (e) { setErr(errorText(e)); } finally { setBusy(""); }
  }
  async function finish(kind: "submit" | "skip") {
    if (kind === "skip" && !confirm("Skip this task? It will count as not attempted.")) return;
    if (kind === "submit" && !confirm("Submit your solution? You cannot change it afterwards.")) return;
    setBusy(kind); setErr("");
    try {
      await api(`${base}/task/${kind}`, kind === "submit" ? { json: body() } : { method: "POST" });
      onDone();
    } catch (e) { setErr(errorText(e)); setBusy(""); }
  }
  const reportPaste = (e: React.ClipboardEvent) => {
    const n = e.clipboardData.getData("text").length;
    if (n > 0) api(`${base}/task/event`, { json: { kind: "paste", chars: n } }).catch(() => {});
  };

  const p = task.problem;
  return (
    <section className="card" aria-label={isSql ? "SQL question" : "Coding problem"}>
      <div className="row spread">
        <h2 style={{ margin: 0 }}>{isSql ? "SQL question" : "Coding problem"}: {p.question}</h2>
        <span className={`timer ${left < 60 ? "low" : ""}`} aria-label="Time left for this task">{mmss(left)} left</span>
      </div>
      <div className="split">
        <div>
          <p style={{ whiteSpace: "pre-wrap" }}>{p.statement ?? p.question}</p>
          {!isSql && p.signature && <pre className="code">{p.signature}</pre>}
          {!isSql && p.io && typeof p.io === "object" && (<><h3>Input</h3><p className="small">{p.io.input}</p><h3>Output</h3><p className="small">{p.io.output}</p></>)}
          {!isSql && (p.sample_tests ?? []).map((t, i) => (
            <div key={i}><h3>Example {i + 1}</h3><div className="small muted">Input</div><pre className="code">{t.stdin || "(empty)"}</pre><div className="small muted">Expected output</div><pre className="code">{t.expected}</pre></div>
          ))}
          {isSql && (
            <>
              <h3>Tables</h3><pre className="code">{(p.schema ?? []).join(";\n")}</pre>
              {Object.entries(p.sample_rows ?? {}).map(([name, rows]) => (<div key={name}><div className="small muted">{name} (sample rows)</div><pre className="code">{(rows as unknown[][]).map((r) => r.map(cell).join(" | ")).join("\n")}</pre></div>))}
              <h3>Expected result for this sample</h3>
              <pre className="code">{(p.columns ?? []).join(" | ")}{"\n"}{(p.sample_expected ?? []).map((r) => r.map(cell).join(" | ")).join("\n")}</pre>
            </>
          )}
        </div>
        <div>
          {!isSql && (
            <div className="row" style={{ marginBottom: 6 }}>
              <label htmlFor="lang" style={{ margin: 0 }}>Language</label>
              <select id="lang" value={lang} onChange={(e) => setLang(e.target.value)} style={{ width: "auto" }}>{langs.map((l) => <option key={l} value={l}>{l === "c" ? "C" : l[0].toUpperCase() + l.slice(1)}</option>)}</select>
            </div>
          )}
          <textarea className="code" aria-label={isSql ? "SQL query" : "Code editor"} spellCheck={false} value={current} onPaste={reportPaste}
            onChange={(e) => setCode((c) => ({ ...c, [isSql ? "sql" : lang]: e.target.value }))}
            onKeyDown={(e) => {
              if (e.key === "Tab") { // a tab in the editor, not a jump to the next control
                e.preventDefault();
                const el = e.currentTarget, s = el.selectionStart;
                const v = el.value.slice(0, s) + "    " + el.value.slice(el.selectionEnd);
                setCode((c) => ({ ...c, [isSql ? "sql" : lang]: v }));
                requestAnimationFrame(() => { el.selectionStart = el.selectionEnd = s + 4; });
              }
            }} />
          {err && <p className="err" role="alert">{err}</p>}
          <div className="row">
            <button disabled={!!busy || !current.trim()} onClick={run}>{busy === "run" ? "Running…" : isSql ? "Run on the sample" : "Run sample tests"}</button>
            <button className="primary" disabled={!!busy} onClick={() => finish("submit")}>{busy === "submit" ? "Submitting…" : "Submit"}</button>
            <button className="danger" disabled={!!busy} onClick={() => finish("skip")}>I don&apos;t know</button>
          </div>
          <p className="muted small">Run uses the visible examples only. Submit is final and is checked against more tests you cannot see.</p>

          {samples && (
            <div role="status">
              {samples.length === 0 && <p>No sample tests.</p>}
              {samples.map((s, i) => (
                <div key={i} className="card" style={{ margin: "8px 0" }}>
                  <strong className={s.passed ? "pass" : "fail"}>Example {i + 1}: {s.passed ? "passed" : "failed"}</strong>
                  {!s.passed && <><div className="small muted">Your output</div><pre className="code">{s.actual || "(nothing)"}</pre><div className="small muted">Expected</div><pre className="code">{s.expected}</pre></>}
                  {s.error && <pre className="code">{s.error}</pre>}
                </div>
              ))}
            </div>
          )}
          {sql && (
            <div role="status">
              {!sql.ok ? <p className="err">{sql.error}</p> : (
                <>
                  <strong className={sql.matches_expected ? "pass" : "fail"}>{sql.matches_expected ? "Matches the expected sample result" : "Does not match the expected sample result"}</strong>
                  <pre className="code">{(sql.columns ?? []).join(" | ")}{"\n"}{(sql.rows ?? []).map((r) => r.map(cell).join(" | ")).join("\n")}</pre>
                </>
              )}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
