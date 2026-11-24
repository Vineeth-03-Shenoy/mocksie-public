// Microphone capture for Mocksie voice. Runs on the audio thread.
// Takes whatever sample rate the browser gives us (usually 48000 or 44100), averages down to 16 kHz mono,
// converts to 16-bit little-endian integers and posts 80 ms frames (1280 samples = 2560 bytes), the size Deepgram Flux recommends.
// It also posts a smoothed loudness number so the orb can react.
class MicCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000;
    this.acc = 0;
    this.n = 0;
    this.pos = 0;
    this.frame = new Int16Array(1280);
    this.fill = 0;
    this.level = 0;
    this.sinceLevel = 0;
  }

  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    for (let i = 0; i < ch.length; i++) {
      const s = ch[i];
      this.acc += s;
      this.n += 1;
      this.level = Math.max(this.level * 0.995, Math.abs(s));
      this.pos += 1;
      if (this.pos >= this.ratio) {
        this.pos -= this.ratio;
        const v = Math.max(-1, Math.min(1, this.acc / this.n));
        this.acc = 0;
        this.n = 0;
        this.frame[this.fill] = v < 0 ? v * 0x8000 : v * 0x7fff;
        this.fill += 1;
        if (this.fill >= this.frame.length) {
          const out = this.frame.slice();
          this.port.postMessage({ pcm: out.buffer }, [out.buffer]);
          this.fill = 0;
        }
      }
    }
    this.sinceLevel += ch.length;
    if (this.sinceLevel >= 2048) {
      this.port.postMessage({ level: this.level });
      this.sinceLevel = 0;
    }
    return true;
  }
}

registerProcessor("mic-capture", MicCapture);
