// Browser side of the voice interview.
//   microphone -> (echo cancellation, noise suppression, auto gain ON) -> 16 kHz 16-bit frames -> our backend websocket
//   our backend websocket -> 24 kHz 16-bit speech frames -> played here, gaplessly
// The Deepgram key never comes to the browser: this talks only to our own server.

import { apiBase } from "./api";

export type VoiceState = "connecting" | "listening" | "thinking" | "speaking" | "paused" | "ended" | "fallback";

export type VoiceEvents = {
  onState: (s: VoiceState) => void;
  onLevel: (mic: number, speaker: number) => void;
  onError: (message: string) => void;
  onTurnDone?: () => void;       // the interviewer finished speaking (the page refreshes the transcript)
};

export const MIC_CONSTRAINTS: MediaTrackConstraints = { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 };

export function wsBase(): string {
  return apiBase().replace(/^http/, "ws");
}

export async function openMic(): Promise<MediaStream> {
  if (!navigator.mediaDevices?.getUserMedia) throw new Error("This browser cannot use the microphone. Please type your answers instead.");
  try {
    return await navigator.mediaDevices.getUserMedia({ audio: MIC_CONSTRAINTS });
  } catch (e) {
    const name = e instanceof DOMException ? e.name : "";
    if (name === "NotAllowedError") throw new Error("Microphone permission was denied. Allow it in the browser's address bar, or type your answers instead.");
    if (name === "NotFoundError") throw new Error("No microphone was found. Plug one in, or type your answers instead.");
    throw new Error("The microphone could not be started. Please type your answers instead.");
  }
}

/** Plays 16-bit mono PCM chunks back to back and reports when everything queued has finished. */
export class Player {
  private ctx: AudioContext;
  private analyser: AnalyserNode;
  private next = 0;
  private active = 0;
  private sources = new Set<AudioBufferSourceNode>();
  private waiting: (() => void) | null = null;
  private data: Uint8Array<ArrayBuffer>;

  constructor(private rate: number) {
    this.ctx = new AudioContext();
    this.analyser = this.ctx.createAnalyser();
    this.analyser.fftSize = 512;
    this.analyser.connect(this.ctx.destination);
    this.data = new Uint8Array(this.analyser.fftSize);
  }

  async resume() {
    if (this.ctx.state === "suspended") await this.ctx.resume();
  }

  push(pcm: ArrayBuffer) {
    const ints = new Int16Array(pcm.byteLength % 2 ? pcm.slice(0, pcm.byteLength - 1) : pcm);   // whole 16-bit samples only
    if (!ints.length) return;
    const buf = this.ctx.createBuffer(1, ints.length, this.rate);
    const ch = buf.getChannelData(0);
    for (let i = 0; i < ints.length; i++) ch[i] = ints[i] / 32768;
    const src = this.ctx.createBufferSource();
    src.buffer = buf;
    src.connect(this.analyser);
    const at = Math.max(this.ctx.currentTime + 0.03, this.next);
    src.start(at);
    this.next = at + buf.duration;
    this.active += 1;
    this.sources.add(src);
    src.onended = () => {
      this.sources.delete(src);
      this.active -= 1;
      if (this.active === 0) this.drained();
    };
  }

  private drained() {
    const w = this.waiting;
    this.waiting = null;
    if (w) w();
  }

  /** Resolves once everything pushed so far has been played (immediately if nothing is playing). */
  whenDrained(): Promise<void> {
    return this.active === 0 ? Promise.resolve() : new Promise((res) => { this.waiting = res; });
  }

  stop() {
    for (const s of this.sources) { try { s.stop(); } catch { /* already stopped */ } }
    this.sources.clear();
    this.active = 0;
    this.next = 0;
    this.drained();
  }

  level(): number {
    this.analyser.getByteTimeDomainData(this.data);
    let peak = 0;
    for (const v of this.data) peak = Math.max(peak, Math.abs(v - 128));
    return peak / 128;
  }

  close() {
    this.stop();
    void this.ctx.close();
  }
}

export class VoiceClient {
  private ws: WebSocket | null = null;
  private stream: MediaStream | null = null;
  private ctx: AudioContext | null = null;
  private player: Player | null = null;
  private mic = 0;
  private timer: ReturnType<typeof setInterval> | null = null;
  private state: VoiceState = "connecting";
  private closed = false;

  constructor(private token: string, private ev: VoiceEvents) {}

  private set(s: VoiceState) {
    if (this.state === s) return;
    this.state = s;
    this.ev.onState(s);
  }

  async start() {
    this.stream = await openMic();
    const ctx = new AudioContext();
    this.ctx = ctx;
    await ctx.audioWorklet.addModule("/voice-worklet.js");
    const node = new AudioWorkletNode(ctx, "mic-capture");
    ctx.createMediaStreamSource(this.stream).connect(node);   // not connected to the speakers: we never play the mic back
    node.port.onmessage = (m: MessageEvent) => {
      if (m.data.pcm && this.ws?.readyState === WebSocket.OPEN) this.ws.send(m.data.pcm);
      if (typeof m.data.level === "number") this.mic = m.data.level;
    };
    const ws = new WebSocket(`${wsBase()}/api/session/${this.token}/voice`);
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    ws.onmessage = (m) => this.onMessage(m);
    ws.onclose = () => { if (!this.closed && this.state !== "ended" && this.state !== "fallback") { this.ev.onError("The voice connection closed."); this.set("fallback"); } };
    ws.onerror = () => { if (!this.closed) this.ev.onError("The voice connection failed."); };
    this.timer = setInterval(() => this.ev.onLevel(this.mic, this.player?.level() ?? 0), 60);
  }

  private async onMessage(m: MessageEvent) {
    if (m.data instanceof ArrayBuffer) {
      this.player?.push(m.data);
      return;
    }
    const msg = JSON.parse(m.data as string) as { type: string; message?: string; tts_rate?: number };
    switch (msg.type) {
      case "config":
        this.player = new Player(msg.tts_rate ?? 24000);
        await this.player.resume();
        this.set("thinking");
        break;
      case "tts_start": this.set("speaking"); break;
      case "tts_end": await this.player?.whenDrained(); this.send({ type: "playback_ended" }); this.ev.onTurnDone?.(); this.set("listening"); break;
      case "stop_playback": this.player?.stop(); this.set("listening"); break;
      case "thinking": this.set("thinking"); break;
      case "paused": this.set("paused"); break;
      case "ended": await this.player?.whenDrained(); this.ev.onTurnDone?.(); this.set("ended"); break;
      case "error": this.ev.onError(msg.message ?? "Something went wrong."); break;
      case "fallback": this.ev.onError(msg.message ?? "Voice is not available."); this.set("fallback"); break;
    }
  }

  send(msg: { type: string }) {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(msg));
  }

  done() { this.send({ type: "done" }); }          // "I'm done answering"
  resume() { this.send({ type: "resume" }); }      // the coding / SQL panel closed

  stop() {
    this.closed = true;
    this.send({ type: "stop" });
    if (this.timer) clearInterval(this.timer);
    this.ws?.close();
    this.stream?.getTracks().forEach((t) => t.stop());
    void this.ctx?.close();
    this.player?.close();
  }
}
