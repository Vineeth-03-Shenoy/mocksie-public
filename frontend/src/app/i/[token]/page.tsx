"use client";

import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, errorText, mmss, type SessionView, type StepResult, type Task } from "@/lib/api";
import TaskPanel from "./TaskPanel";
import VoicePanel from "./VoicePanel";

const SILENCE_MS = 90_000; // no reply for this long: the server nudges, then counts a miss

export default function Interview() {
  const { token } = useParams<{ token: string }>();
  const base = `/api/session/${token}`;
  const [view, setView] = useState<SessionView | null>(null);
  const [err, setErr] = useState("");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [left, setLeft] = useState(0);
  const [voiceOn, setVoiceOn] = useState(false);   // while voice is on the server runs the answer timers and the text box is hidden
  const endRef = useRef<HTMLDivElement>(null);
  const silence = useRef<ReturnType<typeof setTimeout> | null>(null);

  const refresh = useCallback(async () => {
    try {
      const v = await api<SessionView>(base);
      setView(v);
      setLeft(v.remaining_s);
      setErr("");
    } catch (e) {
      setErr(errorText(e));
    }
  }, [base]);

  useEffect(() => { refresh(); }, [refresh]);
  useEffect(() => {  // keep in step with the server (it enforces the timers and the auto-submit)
    if (!view || view.status !== "active") return;
    const t = setInterval(refresh, 7000);
    return () => clearInterval(t);
  }, [view?.status, refresh]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const t = setInterval(() => setLeft((s) => Math.max(0, s - 1)), 1000);
    return () => clearInterval(t);
  }, []);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [view?.messages.length]);

  const waiting = !voiceOn && !!view && view.status === "active" && !view.task && view.messages.at(-1)?.role === "interviewer";
  useEffect(() => {  // the silence timer only runs while it is the candidate's turn
    if (silence.current) clearTimeout(silence.current);
    if (!waiting) return;
    silence.current = setTimeout(async () => { try { await api<StepResult>(`${base}/silence`, { method: "POST" }); await refresh(); } catch { /* ignore */ } }, SILENCE_MS);
    return () => { if (silence.current) clearTimeout(silence.current); };
  }, [waiting, view?.messages.length, base, refresh]);

  async function step(path: string, json?: unknown) {
    setBusy(true);
    setErr("");
    try {
      await api<StepResult>(`${base}${path}`, json === undefined ? { method: "POST" } : { json });
      await refresh();
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  async function send(e: React.FormEvent) {
    e.preventDefault();
    const t = text.trim();
    if (!t || busy) return;
    setText("");
    setView((v) => (v ? { ...v, messages: [...v.messages, { role: "candidate", text: t }] } : v));
    await step("/message", { text: t });
  }

  if (!view) return <main className="wrap narrow">{err ? <p className="err" role="alert">{err}</p> : <p className="muted">Loading…</p>}</main>;

  if (view.status === "created") {
    return (
      <main className="wrap narrow">
        <h1>Mock interview</h1>
        <div className="card">
          <p>This is a practice interview of about 40 minutes. You will chat with an AI interviewer, then solve a coding problem and possibly a short SQL question.</p>
          <ul className="plain">
            <li>Your answers are processed by an AI service. Your name, contact details and resume identifiers are hidden from it.</li>
            <li>Only the interviewer sees your results. You will not see a score.</li>
            <li>Please do not paste code or answers from elsewhere.</li>
          </ul>
          {err && <p className="err" role="alert">{err}</p>}
          <button className="primary" disabled={busy} onClick={() => step("/start")}>{busy ? "Starting…" : "I understand, start"}</button>
        </div>
      </main>
    );
  }

  if (view.status !== "active") {
    return (
      <main className="wrap narrow">
        <h1>Thank you!</h1>
        <div className="card"><p>The interview is finished. Your interviewer will go through the feedback with you.</p><p className="muted small">You can close this page.</p></div>
      </main>
    );
  }

  return (
    <main className="wrap">
      <div className="row spread">
        <h1>Mock interview</h1>
        <span className={`timer ${left < 300 ? "low" : ""}`} aria-label="Time left in the interview">Interview time left {mmss(left)}</span>
      </div>
      {err && <p className="err" role="alert">{err}</p>}
      <VoicePanel token={token} taskOpen={!!view.task} onActive={setVoiceOn} onRefresh={refresh} />
      {view.task ? (
        <TaskPanel key={view.task.problem.id} base={base} task={view.task as Task} onDone={refresh} />
      ) : null}
      <section className="card" aria-live="polite">
        <div className="chat" role="log">
          {view.messages.map((m, i) => <div key={i} className={`msg ${m.role}`}>{m.text}</div>)}
          {busy && !view.task && <div className="msg interviewer muted">…</div>}
          <div ref={endRef} />
        </div>
        {!view.task && !voiceOn && (
          <form onSubmit={send} className="row" style={{ marginTop: 12, alignItems: "flex-end" }}>
            <textarea aria-label="Your answer" value={text} onChange={(e) => setText(e.target.value)} disabled={busy} rows={3} maxLength={4000} placeholder="Type your answer…"
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); (e.currentTarget.form as HTMLFormElement).requestSubmit(); } }} style={{ flex: 1, minWidth: 220 }} />
            <button className="primary" disabled={busy || !text.trim()}>Send</button>
          </form>
        )}
        {view.task && <p className="muted small">The conversation is paused while you work on the task above.</p>}
      </section>
    </main>
  );
}
