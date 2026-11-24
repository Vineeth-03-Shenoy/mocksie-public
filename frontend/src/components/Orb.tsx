"use client";

import type { VoiceState } from "@/lib/voice";

const LABEL: Record<VoiceState, string> = {
  connecting: "Connecting…", listening: "Listening", thinking: "Thinking…", speaking: "Speaking", paused: "Paused while you work on the task",
  ended: "Finished", fallback: "Voice is off: type your answers",
};

/** A soft orb: it swells with the microphone while listening and with the interviewer's voice while speaking. */
export default function Orb({ state, level }: { state: VoiceState; level: number }) {
  const scale = state === "listening" || state === "speaking" ? 1 + Math.min(level, 1) * 0.45 : state === "thinking" ? 1.06 : 1;
  return (
    <div className="orb-wrap" role="status" aria-live="polite" aria-label={LABEL[state]}>
      <div className={`orb ${state}`} style={{ transform: `scale(${scale})` }} />
      <div className="muted small">{LABEL[state]}</div>
    </div>
  );
}
