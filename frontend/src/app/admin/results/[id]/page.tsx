"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { api, apiBase, errorText, type Criterion, type Results, type TextField } from "@/lib/api";

async function copy(text: string) {
  await navigator.clipboard.writeText(text);
}

function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  return <button type="button" onClick={async () => { await copy(text); setDone(true); setTimeout(() => setDone(false), 1200); }}>{done ? "Copied" : label}</button>;
}

export default function ResultsPage() {
  const { id } = useParams<{ id: string }>();
  const [r, setR] = useState<Results | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api<Results>(`/api/admin/interviews/${id}/results`).then((x) => { setR(x); setErr(""); }).catch((e) => setErr(errorText(e)));
  }, [id]);
  useEffect(() => {
    load();
    const t = setInterval(() => setR((cur) => { if (cur && cur.scoring.state !== "ready") load(); return cur; }), 6000);
    return () => clearInterval(t);
  }, [load]);

  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    setErr("");
    try { await fn(); load(); } catch (e) { setErr(errorText(e)); } finally { setBusy(false); }
  }

  if (!r) return <main className="wrap">{err ? <p className="err">{err} <Link href="/admin">Back</Link></p> : <p className="muted">Loading…</p>}</main>;
  const locked = r.status === "finalized";

  return (
    <main className="wrap">
      <p><Link href="/admin">← Console</Link></p>
      <div className="row spread">
        <div><h1>{r.candidate_name}</h1><span className={`pill ${r.status_label}`}>{r.status_label}</span>{locked && r.finalized_at && <span className="muted small"> · finalized {new Date(r.finalized_at * 1000).toLocaleString()}</span>}</div>
        {r.view && (
          <div className="row">
            <a className="btn" href={`${apiBase()}/api/admin/interviews/${r.id}/results.csv`}>Export CSV</a>
            {!locked && <button className="primary" disabled={busy} onClick={() => { if (confirm("Finalize locks these results. Continue?")) act(() => api(`/api/admin/interviews/${id}/finalize`, { method: "POST" })); }}>Finalize</button>}
          </div>
        )}
      </div>
      {err && <p className="err" role="alert">{err}</p>}

      {!r.view ? (
        <div className="card">
          {r.scoring.state === "queued" && <p>The judge allowance for today is nearly used, so scoring is queued. Nothing is lost. {r.scoring.message}</p>}
          {r.scoring.state === "failed" && <p className="err">{r.scoring.message}</p>}
          {(r.scoring.state === "running" || r.scoring.state === null) && <p>{r.status === "created" || r.status === "active" ? "The interview is not finished yet." : "Scoring is running…"}</p>}
          {(r.scoring.state === "queued" || r.scoring.state === "failed") && <button className="primary" disabled={busy} onClick={() => act(() => api(`/api/admin/interviews/${id}/score`, { method: "POST" }))}>{r.scoring.state === "queued" ? "Score now" : "Retry"}</button>}
        </div>
      ) : (
        <>
          <p className="muted small">Same order as the college form. AI values show their evidence and confidence; type a number to override, then copy into the college form. Appearance and body language are for you to fill.</p>
          <div className="card" style={{ overflowX: "auto" }}>
            <table>
              <thead><tr><th>Criterion</th><th>AI</th><th>Evidence</th><th>Your value</th><th></th></tr></thead>
              <tbody>{r.view.criteria.map((c) => <CriterionRow key={c.id} c={c} locked={locked} id={id} onSaved={load} />)}</tbody>
            </table>
          </div>

          <div className="card">
            <p style={{ margin: 0 }}><strong>Overall score: {r.view.overall_score ?? "—"} / 10</strong>{r.view.provisional && <span className="badge-low"> provisional</span>}</p>
            <p className="muted small" style={{ margin: "4px 0 0" }}>{r.view.formula}{r.view.provisional && ` Still to enter: ${r.view.missing_inputs.join("; ")}. Until then the mean uses only the criteria that have a value.`}</p>
          </div>

          <div className="card">
            <div className="row spread"><h3 style={{ margin: 0 }}>Overall assessment</h3><CopyButton text={r.view.overall_assessment ?? ""} /></div>
            <p><strong>{r.view.overall_assessment ?? "—"}</strong> <span className="muted">(score {r.view.overall_score ?? "—"}; AI suggested: {r.view.overall_assessment_ai ?? "—"})</span></p>
            <Choice label="Override" current={r.view.overall_assessment} options={r.options?.assessment ?? []} disabled={locked} onPick={(v) => act(() => api(`/api/admin/interviews/${id}/overrides`, { method: "PUT", json: { kind: "assessment", value: v } }))} />
          </div>

          {r.view.text_fields.map((t) => <TextCard key={t.label} t={t} locked={locked} id={id} onSaved={load} />)}

          <div className="card">
            <div className="row spread"><h3 style={{ margin: 0 }}>Recommendation</h3><CopyButton text={r.view.recommendation} /></div>
            <p><strong>{r.view.recommendation}</strong> <span className="muted">(AI suggested: {r.view.recommendation_ai})</span></p>
            <Choice label="Override" current={r.view.recommendation} options={r.options?.recommendation ?? []} disabled={locked} onPick={(v) => act(() => api(`/api/admin/interviews/${id}/overrides`, { method: "PUT", json: { kind: "recommendation", value: v } }))} />
          </div>

          {r.integrity && r.integrity.length > 0 && (
            <div className="card"><h3 style={{ marginTop: 0 }}>Integrity flags</h3><ul className="plain">{r.integrity.map((f, i) => <li key={i}>{f}</li>)}</ul></div>
          )}
          {r.warnings && r.warnings.length > 0 && <p className="muted small">{r.warnings.join(" ")}</p>}

          <h2>Coding and SQL</h2>
          <div className="card">
            {(r.coding ?? []).map((c, i) => (
              <details key={i}>
                <summary><strong>{c.kind.toUpperCase()} (level {c.level})</strong> — {c.pct}%{c.tests_total ? ` · ${c.tests_passed}/${c.tests_total} tests` : ""}{c.datasets_total ? ` · ${c.datasets_passed}/${c.datasets_total} datasets` : ""}{c.skipped ? " · skipped" : ""}{c.auto_submitted ? " · auto-submitted at zero" : ""}{c.timeouts ? ` · ${c.timeouts} timed out` : ""}</summary>
                {c.compile_error && <pre className="code">{c.compile_error}</pre>}
                {c.code ? <pre className="code">{c.code}</pre> : <p className="muted">No code submitted.</p>}
              </details>
            ))}
            {(r.coding ?? []).length === 0 && <p className="muted">No coding task was reached.</p>}
          </div>

          <h2>Question by question</h2>
          <div className="card" style={{ overflowX: "auto" }}>
            <table>
              <thead><tr><th>Question</th><th>Score</th><th>Evidence</th></tr></thead>
              <tbody>
                {(r.questions ?? []).map((q) => (
                  <tr key={q.id}>
                    <td><strong>{q.question}</strong><div className="muted small">{q.state.replace("_", " ")} · {q.topic}</div></td>
                    <td>{q.skipped ? "skipped (0)" : q.score ?? "—"}{q.needs_review && <div className="badge-low small">graders disagreed</div>}</td>
                    <td className="small">{q.evidence.map((e, i) => <div key={i}>“{e}”</div>)}{q.skipped && <span className="muted">no answer</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {r.prep_references && r.prep_references.length > 0 && (
            <><h2>Prep book references for the student</h2><div className="card"><ul className="plain">{r.prep_references.map((p, i) => <li key={i}>{p.label}</li>)}</ul></div></>
          )}

          <details className="card"><summary><strong>Transcript (redacted)</strong></summary>
            {(r.transcript ?? []).map((t, i) => <p key={i}><span className="muted small">{t.role}:</span> {t.text}</p>)}
          </details>
        </>
      )}
    </main>
  );
}

function CriterionRow({ c, locked, id, onSaved }: { c: Criterion; locked: boolean; id: string; onSaved: () => void }) {
  const [val, setVal] = useState(c.override !== null ? String(c.override) : "");
  const [note, setNote] = useState(c.override_note);
  const [err, setErr] = useState("");
  const save = async (value: number | null) => {
    setErr("");
    try { await api(`/api/admin/interviews/${id}/overrides`, { method: "PUT", json: { kind: "criterion", key: c.id, value, note } }); onSaved(); } catch (e) { setErr(errorText(e)); }
  };
  const [lo, hi] = c.scale;
  return (
    <tr>
      <td><strong>{c.label}</strong><div className="muted small">{c.source === "interviewer" ? "you fill this" : c.source === "ai_draft" ? "AI draft: you confirm" : c.id === "overall" ? "calculated" : "counts equally"}</div></td>
      <td>{c.ai_value ?? "—"}{c.confidence && <div className={`small badge-${c.confidence}`}>{c.confidence} confidence</div>}</td>
      <td className="small">{c.evidence.length ? c.evidence.map((e, i) => <div key={i}>{e}</div>) : <span className="muted">—</span>}</td>
      <td>
        {c.id === "overall" ? <span className="muted small">calculated: change the other scores</span> : <>
        <input className="num" inputMode="numeric" aria-label={`Override ${c.label}`} value={val} disabled={locked} placeholder={`${lo}–${hi}`} onChange={(e) => setVal(e.target.value)} />
        <input aria-label={`Note for ${c.label}`} value={note} disabled={locked} placeholder="note (optional)" onChange={(e) => setNote(e.target.value)} style={{ marginTop: 6 }} />
        {!locked && <div className="row" style={{ marginTop: 6 }}>
          <button disabled={!/^\d+$/.test(val)} onClick={() => save(Number(val))}>Save</button>
          {c.override !== null && <button onClick={() => { setVal(""); setNote(""); save(null); }}>Clear</button>}
        </div>}
        {err && <div className="err small">{err}</div>}
        </>}
      </td>
      <td><div>Final: <strong>{c.value ?? "—"}</strong></div><CopyButton text={c.value === null ? "" : String(c.value)} /></td>
    </tr>
  );
}

function TextCard({ t, locked, id, onSaved }: { t: TextField; locked: boolean; id: string; onSaved: () => void }) {
  const [v, setV] = useState(t.override ?? t.ai_value);
  const [err, setErr] = useState("");
  const send = async (value: string | null) => {
    setErr("");
    try { await api(`/api/admin/interviews/${id}/overrides`, { method: "PUT", json: { kind: "text", key: t.label, value } }); onSaved(); } catch (e) { setErr(errorText(e)); }
  };
  return (
    <div className="card">
      <div className="row spread"><h3 style={{ margin: 0 }}>{t.label}</h3><CopyButton text={v} /></div>
      <textarea aria-label={t.label} value={v} disabled={locked} onChange={(e) => setV(e.target.value)} rows={5} />
      <p className="muted small">AI draft is kept; your edits are saved separately.</p>
      {!locked && <div className="row"><button disabled={v === (t.override ?? t.ai_value)} onClick={() => send(v)}>Save my edit</button>{t.override !== null && <button onClick={() => { setV(t.ai_value); send(null); }}>Restore AI draft</button>}</div>}
      {err && <div className="err small">{err}</div>}
    </div>
  );
}

function Choice({ label, current, options, disabled, onPick }: { label: string; current: string | null; options: string[]; disabled: boolean; onPick: (v: string | null) => void }) {
  return (
    <div className="row">
      <label htmlFor={label + options.join()} style={{ margin: 0 }}>{label}</label>
      <select id={label + options.join()} value={current ?? ""} disabled={disabled} onChange={(e) => onPick(e.target.value || null)} style={{ maxWidth: 420 }}>
        <option value="">(use the AI suggestion)</option>
        {options.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    </div>
  );
}
