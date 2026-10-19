// Audio thread of live captions: collects the recorded signal (mixed to mono) and hands it to the
// page in blocks of about 0.25 s. Resampling to 16 kHz and sending happen on the page (app.js).
class PcmCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.block = [];
    this.size = 0;
    this.target = Math.round(sampleRate / 4);
  }

  process(inputs) {
    const input = inputs[0];
    if (input && input.length) {
      const n = input[0].length;
      const mono = new Float32Array(n);
      for (const ch of input) for (let i = 0; i < n; i++) mono[i] += ch[i] / input.length;
      this.block.push(mono);
      this.size += n;
      if (this.size >= this.target) {
        const out = new Float32Array(this.size);
        let pos = 0;
        for (const b of this.block) { out.set(b, pos); pos += b.length; }
        this.port.postMessage(out, [out.buffer]);
        this.block = [];
        this.size = 0;
      }
    }
    return true;
  }
}

registerProcessor('pcm-capture', PcmCapture);
