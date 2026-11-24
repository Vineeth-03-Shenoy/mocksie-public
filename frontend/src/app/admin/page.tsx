"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api, ApiError, errorText, type InterviewRow } from "@/lib/api";

type Created = { id: string; token: string; warnings: string[]; profile: Record<string, unknown> | null };

export default function Console() {
  const [authed, setAuthed] = useState<boolean | null>(null);

  useEffect(() => {
    api("/api/auth/me").then(() => setAuthed(true)).catch((e) => setAuthed(e instanceof ApiError && e.status === 0 ? null : false));
  }, []);

  if (authed === null) return <main className="wrap"><p className="muted">Loading… (if this stays, start the server: <code>python -m backend</code>)</p></main>;
  if (!authed) return <Login onDone={() => setAuthed(true)} />;
  return <Home onLogout={() => setAuthed(false)} />;
}

function Login({ onDone }: { onDone: () => void }) {
  const [pw, setPw] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr("");
    try {
      await api("/api/auth/login", { json: { password: pw } });
      onDone();
    } catch (x) {
      setErr(errorText(x));
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="wrap narrow">
      <h1>Interviewer sign-in</h1>
      <form className="card" onSubmit={submit}>
        <label htmlFor="pw">Password</label>
        <input id="pw" type="password" autoComplete="current-password" value={pw} onChange={(e) => setPw(e.target.value)} autoFocus />
        {err && <p className="err" role="alert">{err}</p>}
        <p><button className="primary" disabled={busy || !pw}>{busy ? "Signing in…" : "Sign in"}</button></p>
      </form>
    </main>
  );
}

function Home({ onLogout }: { onLogout: () => void }) {
  const [rows, setRows] = useState<InterviewRow[]>([]);
  const [err, setErr] = useState("");
  const load = useCallback(() => {
    api<InterviewRow[]>("/api/admin/interviews").then(setRows).catch((e) => setErr(errorText(e)));
  }, []);
  useEffect(() => {
    load();
    const t = setInterval(load, 8000);
    return () => clearInterval(t);
  }, [load]);

  return (
    <main className="wrap">
      <div className="row spread">
        <h1>Mocksie console</h1>
        <button onClick={async () => { await api("/api/auth/logout", { method: "POST" }).catch(() => {}); onLogout(); }}>Sign out</button>
      </div>
      <NewInterview onCreated={load} />
      <Settings />
      <h2>Interviews</h2>
      {err && <p className="err">{err}</p>}
      <div className="card">
        {rows.length === 0 ? <p className="muted">No interviews yet.</p> : (
          <table>
            <thead><tr><th>Candidate</th><th>Status</th><th>Started</th><th></th></tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id}>
                  <td>{r.candidate_name}</td>
                  <td><span className={`pill ${r.status_label.split(" ")[0]}`}>{r.status_label}</span></td>
                  <td className="muted small">{new Date(r.created_at * 1000).toLocaleString()}</td>
                  <td className="row">
                    {["Ready", "Finalized", "Scoring"].includes(r.status_label) && <Link className="btn" href={`/admin/results/${r.id}`}>Results</Link>}
                    {r.status === "created" && <CopyLink token={r.token} />}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </main>
  );
}

const linkFor = (token: string) => `${window.location.origin}/i/${token}`;

function CopyLink({ token }: { token: string }) {
  const [done, setDone] = useState(false);
  return (
    <>
      <button onClick={async () => { await navigator.clipboard.writeText(linkFor(token)); setDone(true); setTimeout(() => setDone(false), 1500); }}>{done ? "Copied" : "Copy candidate link"}</button>
      <a className="btn" href={`/i/${token}`} target="_blank" rel="noreferrer">Open here</a>
    </>
  );
}

type Voice = { eot_threshold: number; eager_eot_threshold: number | null; eot_timeout_ms: number; barge_in: boolean; no_answer_s: number; answer_cap_s: number; voice: string };
type SettingsData = { company_faq: string; voice: Voice; voices: string[] };

function Settings() {
  const [faq, setFaq] = useState("");
  const [savedFaq, setSavedFaq] = useState("");
  const [voice, setVoice] = useState<Voice | null>(null);
  const [voices, setVoices] = useState<string[]>([]);
  const [msg, setMsg] = useState("");
  useEffect(() => {
    api<SettingsData>("/api/admin/settings").then((r) => { setFaq(r.company_faq); setSavedFaq(r.company_faq); setVoice(r.voice); setVoices(r.voices); }).catch(() => {});
  }, []);
  async function save(body: { company_faq?: string; voice?: Voice }) {
    setMsg("");
    try {
      const r = await api<SettingsData>("/api/admin/settings", { method: "PUT", json: body });
      setSavedFaq(r.company_faq); setVoice(r.voice); setMsg("Saved.");
    } catch (e) { setMsg(errorText(e)); }
  }
  const num = (k: keyof Voice) => (e: React.ChangeEvent<HTMLInputElement>) => voice && setVoice({ ...voice, [k]: e.target.value === "" ? null : Number(e.target.value) });
  return (
    <details className="card">
      <summary><strong>Settings</strong>: company FAQ and voice</summary>
      <h3>Company FAQ</h3>
      <p className="muted small">Optional. When a candidate asks about the role, team, training or hiring process, the interviewer answers from the job description plus this text. If the answer is not here it says so honestly and generally. Do not put emails, phone numbers or links here: this text is sent to the AI service.</p>
      <label htmlFor="faq">Company FAQ</label>
      <textarea id="faq" rows={6} maxLength={3000} value={faq} onChange={(e) => setFaq(e.target.value)} placeholder="e.g. Freshers join a 6 week training programme. Teams: payments, search, platform." />
      <div className="row"><button disabled={faq === savedFaq} onClick={() => save({ company_faq: faq })}>Save FAQ</button></div>

      {voice && (
        <>
          <h3>Voice</h3>
          <p className="muted small">Speech recognition is Deepgram Flux, speech is Deepgram Aura-2. Changes apply to interviews that start voice after you save.</p>
          <div className="split">
            <div>
              <label htmlFor="eot">End-of-turn confidence (0.5 to 1.0)</label>
              <input id="eot" type="number" step="0.05" min={0.5} max={1} value={voice.eot_threshold} onChange={num("eot_threshold")} />
              <label htmlFor="eager">Eager end-of-turn (0.3 to 0.9, blank = off)</label>
              <input id="eager" type="number" step="0.05" min={0.3} max={0.9} value={voice.eager_eot_threshold ?? ""} onChange={num("eager_eot_threshold")} />
              <label htmlFor="eott">Pause that ends a turn, ms (500 to 60000)</label>
              <input id="eott" type="number" step="500" min={500} max={60000} value={voice.eot_timeout_ms} onChange={num("eot_timeout_ms")} />
            </div>
            <div>
              <label htmlFor="noans">Seconds of silence before a nudge (after the question is spoken)</label>
              <input id="noans" type="number" min={5} max={120} value={voice.no_answer_s} onChange={num("no_answer_s")} />
              <label htmlFor="cap">Longest single answer, seconds</label>
              <input id="cap" type="number" min={30} max={600} value={voice.answer_cap_s} onChange={num("answer_cap_s")} />
              <label htmlFor="vc">Interviewer voice</label>
              <select id="vc" value={voice.voice} onChange={(e) => setVoice({ ...voice, voice: e.target.value })}>{voices.map((v) => <option key={v} value={v}>{v}</option>)}</select>
            </div>
          </div>
          <label className="row" style={{ fontWeight: 400 }}><input type="checkbox" style={{ width: "auto" }} checked={voice.barge_in} onChange={(e) => setVoice({ ...voice, barge_in: e.target.checked })} />
            Let the candidate interrupt the interviewer (barge-in). Leave off on a single laptop: the speakers echo into the microphone.</label>
          <div className="row"><button onClick={() => save({ voice })}>Save voice settings</button><a href="/mic-test" target="_blank" rel="noreferrer">Open the microphone and echo check</a></div>
        </>
      )}
      {msg && <p className={msg === "Saved." ? "muted small" : "err small"} role="status">{msg}</p>}
    </details>
  );
}

function NewInterview({ onCreated }: { onCreated: () => void }) {
  const [name, setName] = useState("");
  const [hide, setHide] = useState("");
  const [behav, setBehav] = useState(1);
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [made, setMade] = useState<Created | null>(null);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr("");
    setMade(null);
    try {
      const c = await api<{ id: string; token: string }>("/api/admin/interviews", { json: { candidate_name: name, also_hide: hide, behavioural_questions: behav } });
      let warnings: string[] = [];
      let profile: Record<string, unknown> | null = null;
      if (file) {
        try {
          const r = await api<{ profile: Record<string, unknown>; warnings: string[] }>(`/api/admin/interviews/${c.id}/resume`, { method: "PUT", body: file, contentType: "application/pdf" });
          warnings = r.warnings;
          profile = r.profile;
        } catch (x) {
          warnings = ["The resume was not used: " + errorText(x) + " You can still start without it."];
        }
      }
      setMade({ ...c, warnings, profile });
      onCreated();
    } catch (x) {
      setErr(errorText(x));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card">
      <h2 style={{ marginTop: 0 }}>New interview</h2>
      <form onSubmit={create}>
        <label htmlFor="name">Candidate&apos;s full name (type it from the resume)</label>
        <input id="name" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Priya Rhea Testname" required minLength={2} maxLength={80} />
        <p className="muted small" style={{ margin: "4px 0 0" }}>Used only to hide the name from the AI: every part of it, case-insensitive. Never sent anywhere.</p>
        <label htmlFor="hide">Also hide (optional, comma-separated: college, city, other names)</label>
        <input id="hide" value={hide} onChange={(e) => setHide(e.target.value)} placeholder="e.g. Northfield College, Hyderabad" maxLength={500} />
        <label htmlFor="resume">Resume (PDF, optional)</label>
        <input id="resume" type="file" accept="application/pdf,.pdf" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        <label htmlFor="behav">Behavioural questions</label>
        <select id="behav" value={behav} onChange={(e) => setBehav(Number(e.target.value))}>
          <option value={0}>0</option><option value={1}>1 (default)</option><option value={2}>2</option>
        </select>
        {err && <p className="err" role="alert">{err}</p>}
        <p><button className="primary" disabled={busy || name.trim().length < 2}>{busy ? "Preparing…" : "Create interview"}</button></p>
      </form>
      {made && (
        <div role="status">
          <h3>Ready to start</h3>
          <div className="row"><CopyLink token={made.token} /></div>
          <p className="muted small">The candidate opens the link and presses Start. The link works once the interview has begun; share it only with them.</p>
          {made.warnings.length > 0 && <ul className="plain">{made.warnings.map((w, i) => <li key={i} className="muted">{w}</li>)}</ul>}
          {made.profile && <details><summary>Cleaned profile the AI will use</summary><pre className="code">{JSON.stringify(made.profile, null, 2)}</pre></details>}
        </div>
      )}
    </section>
  );
}
