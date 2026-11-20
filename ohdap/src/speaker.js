const clampRate = (r) => Math.min(1.5, Math.max(0.7, Number(r) || 1));
const FALLBACK_CODES = ['not_granted', 'unavailable', 'unsupported'];

export function createSpeaker({ tts, synth, timers } = {}) {
  const T = timers || { setTimeout: (f, ms) => setTimeout(f, ms), clearTimeout: (id) => clearTimeout(id) };
  const lineFns = [], endFns = [];
  const st = { lines: [], idx: 0, rate: 1, gen: 0, paused: false, timer: null, playing: false };
  const sp = { mode: tts ? 'tts' : synth ? 'synth' : 'timer' };

  const emitLine = (i) => { st.idx = i; lineFns.forEach((f) => f(i)); };
  const emitEnd = () => { st.playing = false; endFns.forEach((f) => f()); };

  if (tts && typeof tts.onLine === 'function') {
    tts.onLine((e) => {
      if (sp.mode !== 'tts' || !st.playing) return;
      const i = Number(e && e.id);
      if (Number.isFinite(i)) emitLine(i);
    });
  }

  function clearTimer() { if (st.timer != null) { T.clearTimeout(st.timer); st.timer = null; } }

  function haltCurrent() {
    clearTimer();
    if (sp.mode === 'tts' && tts) { try { tts.stop(); } catch (e) { /* ignore */ } }
    if (sp.mode === 'synth' && synth) { try { synth.cancel(); } catch (e) { /* ignore */ } }
  }

  function timerStep(gen) {
    if (gen !== st.gen || st.paused) return;
    const text = st.lines[st.idx] || '';
    const ms = Math.max(1500, text.length * 120) / st.rate;
    st.timer = T.setTimeout(() => {
      st.timer = null;
      if (gen !== st.gen) return;
      if (st.idx + 1 >= st.lines.length) { emitEnd(); return; }
      emitLine(st.idx + 1);
      timerStep(gen);
    }, ms);
  }

  function synthLine(gen, i) {
    if (gen !== st.gen) return;
    if (i >= st.lines.length) { emitEnd(); return; }
    emitLine(i);
    const u = new synth.Utterance(st.lines[i]);
    u.lang = 'ko-KR';
    u.rate = st.rate;
    const next = () => synthLine(gen, i + 1);
    u.onend = next;
    u.onerror = next;
    synth.speak(u);
  }

  function start(from) {
    const gen = ++st.gen;
    st.paused = false;
    st.playing = true;
    if (sp.mode === 'tts') {
      emitLine(from);
      const items = st.lines.slice(from).map((text, k) => ({ text, id: String(from + k) }));
      let call;
      try { call = Promise.resolve(tts.speak(items, { language: 'ko', rate: st.rate })); } catch (e) { call = Promise.reject(e); }
      call
        .then(() => { if (gen === st.gen && st.playing) emitEnd(); })
        .catch((e) => {
          if (gen !== st.gen) return;
          if (FALLBACK_CODES.includes(e && e.code)) {
            sp.mode = synth ? 'synth' : 'timer';
            start(st.idx);
          }
        });
    } else if (sp.mode === 'synth') {
      synthLine(gen, from);
    } else {
      emitLine(from);
      timerStep(gen);
    }
  }

  sp.play = (lines, from = 0, rate = st.rate) => {
    haltCurrent();
    st.lines = lines.slice();
    st.rate = clampRate(rate);
    start(Math.max(0, Math.min(from, lines.length - 1)));
  };
  sp.pause = () => {
    st.paused = true;
    if (sp.mode === 'tts') tts.pause && tts.pause();
    else if (sp.mode === 'synth') synth.pause && synth.pause();
    else clearTimer();
  };
  sp.resume = () => {
    if (!st.paused) return;
    st.paused = false;
    if (sp.mode === 'tts') tts.resume && tts.resume();
    else if (sp.mode === 'synth') synth.resume && synth.resume();
    else timerStep(st.gen);
  };
  sp.stop = () => { st.gen++; st.playing = false; haltCurrent(); };
  sp.setRate = (r) => {
    st.rate = clampRate(r);
    if (sp.mode === 'tts' && tts.setRate) tts.setRate(st.rate);
  };
  sp.onLine = (fn) => { lineFns.push(fn); };
  sp.onEnd = (fn) => { endFns.push(fn); };
  sp.isPlaying = () => st.playing && !st.paused;
  return sp;
}

export function browserSynth() {
  if (typeof window === 'undefined' || !window.speechSynthesis || !window.SpeechSynthesisUtterance) return null;
  const s = window.speechSynthesis;
  return { Utterance: window.SpeechSynthesisUtterance, speak: (u) => s.speak(u), cancel: () => s.cancel(), pause: () => s.pause(), resume: () => s.resume() };
}
