"use client";

import { useEffect, useRef, useState } from "react";
import Orb from "@/components/Orb";
import { VoiceClient, type VoiceState } from "@/lib/voice";

/** Voice mode for the interview. The page keeps working by typing if voice is off, fails or is stopped. */
export default function VoicePanel({ token, taskOpen, onActive, onRefresh }: {
  token: string; taskOpen: boolean; onActive: (on: boolean) => void; onRefresh: () => void;
}) {
  const [state, setState] = useState<VoiceState | null>(null);    // null = voice not started
  const [level, setLevel] = useState(0);
  const [err, setErr] = useState("");
  const client = useRef<VoiceClient | null>(null);
  const wasTask = useRef(taskOpen);

  useEffect(() => () => client.current?.stop(), []);
  useEffect(() => {  // the coding / SQL panel closed: tell the server to reopen the speech connections
    if (wasTask.current && !taskOpen) client.current?.resume();
    wasTask.current = taskOpen;
  }, [taskOpen]);

  async function begin() {
    setErr("");
    const c = new VoiceClient(token, {
      onState: (s) => { setState(s); onRefresh(); if (s === "fallback" || s === "ended") onActive(false); },
      onLevel: (mic, speaker) => setLevel(Math.max(mic, speaker)),
      onError: (m) => setErr(m),
      onTurnDone: onRefresh,
    });
    client.current = c;
    try {
      setState("connecting");
      onActive(true);
      await c.start();
    } catch (e) {
      c.stop();
      client.current = null;
      setState(null);
      onActive(false);
      setErr(e instanceof Error ? e.message : "The microphone could not be started.");
    }
  }

  function stop() {
    client.current?.stop();
    client.current = null;
    setState(null);
    setLevel(0);
    onActive(false);
  }

  if (state === null || state === "fallback") {
    return (
      <div className="card">
        <div className="row spread">
          <div><strong>Voice</strong><div className="muted small">Talk to the interviewer instead of typing. Use headphones if you can. Your voice is processed by a speech service.</div></div>
          <button className="primary" onClick={begin}>Use my microphone</button>
        </div>
        {err && <p className="err" role="alert">{err}</p>}
      </div>
    );
  }
  return (
    <div className="card">
      <Orb state={state} level={level} />
      {err && <p className="err small" role="alert" style={{ textAlign: "center" }}>{err}</p>}
      <div className="row" style={{ justifyContent: "center" }}>
        <button disabled={state !== "listening"} onClick={() => client.current?.done()}>I&apos;m done answering</button>
        <button onClick={stop}>Stop voice and type instead</button>
      </div>
    </div>
  );
}
