// Runs on the dedicated audio rendering thread, not the main thread - unlike the
// ScriptProcessorNode it replaces, main-thread jank (DOM updates, SSE parsing, etc.) can't delay
// or drop these callbacks, which was causing mid-utterance gaps in the hands-free live mic.
// Accumulates render quanta (128 samples each) into the same 4096-sample chunks the old
// ScriptProcessor delivered, then posts each chunk to the main thread for VAD/ring-buffer logic.
class MicCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.chunkSize = 4096;
    this.buffer = new Float32Array(this.chunkSize);
    this.writeIndex = 0;
  }

  process(inputs) {
    const input = inputs[0][0];
    if (!input) return true;

    let read = 0;
    while (read < input.length) {
      const toCopy = Math.min(this.chunkSize - this.writeIndex, input.length - read);
      this.buffer.set(input.subarray(read, read + toCopy), this.writeIndex);
      this.writeIndex += toCopy;
      read += toCopy;

      if (this.writeIndex === this.chunkSize) {
        this.port.postMessage(this.buffer.slice());
        this.writeIndex = 0;
      }
    }
    return true;
  }
}

registerProcessor("mic-capture-processor", MicCaptureProcessor);
