"use client";

import { useEffect, useRef, useState } from "react";
import { MIC_CONSTRAINTS, openMic } from "@/lib/voice";

type Verdict = { tone: "good" | "warn" | "bad"; text: string };

// A page to check the microphone and the speaker echo before an interview. Everything stays in this browser tab:
// nothing is recorded, uploaded or sent to any service.
export default function MicTest() {
  const [running, setRunning] = useState(false);
  const [level, setLevel] = useState(0);
  const [peak, setPeak] = useState(0);
  const [device, setDevice] = useState("");
  const [settings, setSettings] = useState<MediaTrackSettings | null>(null);
  const [err, setErr] = useState("");
  const [echo, setEcho] = useState<Verdict | null>(null);
  const [testing, setTesting] = useState(false);
  const stream = useRef<MediaStream | null>(null);
  const ctx = useRef<AudioContext | null>(null);
  const analyser = useRef<AnalyserNode | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const live = useRef(0);

  const read = () => {
    const a = analyser.current;
    if (!a) return 0;
    const d = new Uint8Array(a.fftSize);
    a.getByteTimeDomainData(d);
    let p = 0;
    for (const v of d) p = Math.max(p, Math.abs(v - 128));
    return p / 128;
  };

  async function start() {
    setErr(""); setEcho(null); setPeak(0);
    try {
      const s = await openMic();
      stream.current = s;
      const c = new AudioContext();
      ctx.current = c;
      const an = c.createAnalyser();
      an.fftSize = 1024;
      c.createMediaStreamSource(s).connect(an);
      analyser.current = an;
      const track = s.getAudioTracks()[0];
      setDevice(track.label || "Default microphone");
      setSettings(track.getSettings());
      timer.current = setInterval(() => { const l = read(); live.current = l; setLevel(l); setPeak((p) => Math.max(p * 0.995, l)); }, 50);
      setRunning(true);
    } catch (e) { setErr(e instanceof Error ? e.message : "The microphone could not be started."); }
  }

  function stop() {
    if (timer.current) clearInterval(timer.current);
    stream.current?.getTracks().forEach((t) => t.stop());
    void ctx.current?.close();
    stream.current = null; ctx.current = null; analyser.current = null;
    setRunning(false); setLevel(0);
  }
  useEffect(() => stop, []);  // eslint-disable-line react-hooks/exhaustive-deps

  async function echoTest() {
    const c = ctx.current;
    if (!c) return;
    setTesting(true); setEcho(null);
    const sample = async (ms: number) => { let p = 0; const end = Date.now() + ms; while (Date.now() < end) { p = Math.max(p, read()); await new Promise((r) => setTimeout(r, 30)); } return p; };
    const quiet = await sample(1500);                         // 1. how loud is the room by itself
    const osc = c.createOscillator(); const gain = c.createGain();
    osc.frequency.value = 600; gain.gain.value = 0.35;
    osc.connect(gain).connect(c.destination); osc.start();
    const loud = await sample(2000);                          // 2. the same, while the speakers play a tone
    osc.stop();
    setTesting(false);
    if (loud > Math.max(0.08, quiet * 3)) setEcho({ tone: "bad", text: "Your microphone hears your speakers. Use headphones, or lower the volume. Keep \"barge-in\" off in Settings." });
    else if (loud > Math.max(0.04, quiet * 1.8)) setEcho({ tone: "warn", text: "A little of the speaker sound reaches the microphone. Headphones are safer." });
    else setEcho({ tone: "good", text: "No echo detected. The speakers are not leaking into the microphone." });
  }

  const heard = peak > 0.08;
  return (
    <main className="wrap narrow" style={{ maxWidth: 560, margin: "6vh auto" }}>
      <h1>Microphone and echo check</h1>
      <p className="muted">Nothing is recorded or sent anywhere. The check runs inside this page.</p>
      <div className="card">
        {!running ? <button className="primary" onClick={start}>Start the microphone</button> : <button onClick={stop}>Stop</button>}
        {err && <p className="err" role="alert">{err}</p>}
        {running && (
          <>
            <p className="small muted" style={{ marginBottom: 4 }}>{device}</p>
            <div className="meter" aria-label="Microphone level"><div style={{ width: `${Math.min(100, level * 160)}%` }} /></div>
            <p className={heard ? "pass" : "muted"} role="status">{heard ? "Good: your voice is coming through." : "Say something. The bar should move when you speak."}</p>
            {settings && (
              <p className="small muted">Echo cancellation {String(settings.echoCancellation ?? "unknown")}, noise suppression {String(settings.noiseSuppression ?? "unknown")}, auto gain {String(settings.autoGainControl ?? "unknown")}
                {" "}(asked for: {Object.values(MIC_CONSTRAINTS).slice(0, 3).every(Boolean) ? "all on" : "custom"}).</p>
            )}
            <h3>Speaker echo test</h3>
            <p className="small muted">Plays a short tone through your speakers (keep the volume as you will use it) and listens for it on the microphone. Stay quiet for about four seconds.</p>
            <button disabled={testing} onClick={echoTest}>{testing ? "Testing, stay quiet…" : "Run the echo test"}</button>
            {echo && <p className={echo.tone === "good" ? "pass" : echo.tone === "warn" ? "" : "fail"} role="status" style={{ marginTop: 10 }}>{echo.text}</p>}
          </>
        )}
      </div>
    </main>
  );
}
