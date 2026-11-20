import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createSpeaker } from '../../src/speaker.js';

function fakeTimers() {
  let now = 0, seq = 0;
  const q = new Map();
  return {
    setTimeout(fn, ms) { const id = ++seq; q.set(id, { fn, at: now + ms }); return id; },
    clearTimeout(id) { q.delete(id); },
    advance(ms) {
      const end = now + ms;
      for (;;) {
        let next = null;
        for (const [id, t] of q) if (t.at <= end && (!next || t.at < next[1].at)) next = [id, t];
        if (!next) break;
        q.delete(next[0]); now = next[1].at; next[1].fn();
      }
      now = end;
    },
    pending: () => q.size
  };
}

function fakeTts({ reject } = {}) {
  const t = { spoken: [], lineFns: [], rate: null, paused: false, stopped: 0 };
  t.speak = (lines, opts) => { t.spoken.push({ lines, opts }); return reject ? Promise.reject(reject) : new Promise(() => {}); };
  t.onLine = (fn) => { t.lineFns.push(fn); return () => {}; };
  t.onState = () => () => {};
  t.pause = () => { t.paused = true; };
  t.resume = () => { t.paused = false; };
  t.stop = () => { t.stopped++; };
  t.setRate = (r) => { t.rate = r; };
  return t;
}

test('tts mode speaks the remaining lines with ids and reports line starts', () => {
  const tts = fakeTts();
  const sp = createSpeaker({ tts, synth: null, timers: fakeTimers() });
  assert.equal(sp.mode, 'tts');
  const seen = [];
  sp.onLine((i) => seen.push(i));
  sp.play(['a', 'b', 'c'], 1, 1);
  assert.deepEqual(tts.spoken[0].lines, [{ text: 'b', id: '1' }, { text: 'c', id: '2' }]);
  assert.equal(tts.spoken[0].opts.language, 'ko');
  tts.lineFns.forEach((fn) => fn({ line: 1, id: '2' }));
  assert.ok(seen.includes(2));
});

test('tts refusal falls back to timer mode and continues', async () => {
  const timers = fakeTimers();
  const tts = fakeTts({ reject: { code: 'not_granted' } });
  const sp = createSpeaker({ tts, synth: null, timers });
  const seen = []; let ended = 0;
  sp.onLine((i) => seen.push(i)); sp.onEnd(() => ended++);
  sp.play(['a', 'b'], 0, 1);
  await new Promise((r) => setImmediate(r));
  assert.equal(sp.mode, 'timer');
  timers.advance(10000);
  assert.ok(seen.includes(1));
  assert.equal(ended, 1);
});

test('timer mode advances, pauses, and ends once', () => {
  const timers = fakeTimers();
  const sp = createSpeaker({ tts: null, synth: null, timers });
  assert.equal(sp.mode, 'timer');
  const seen = []; let ended = 0;
  sp.onLine((i) => seen.push(i)); sp.onEnd(() => ended++);
  sp.play(['짧음', '둘', '셋'], 0, 1);
  assert.deepEqual(seen, [0]);
  timers.advance(1500);
  assert.deepEqual(seen, [0, 1]);
  sp.pause();
  timers.advance(60000);
  assert.deepEqual(seen, [0, 1]);
  sp.resume();
  timers.advance(1500);
  timers.advance(1500);
  assert.deepEqual(seen, [0, 1, 2]);
  assert.equal(ended, 1);
});

test('setRate clamps to 0.7..1.5', () => {
  const tts = fakeTts();
  const sp = createSpeaker({ tts, synth: null, timers: fakeTimers() });
  sp.setRate(3); assert.equal(tts.rate, 1.5);
  sp.setRate(0.1); assert.equal(tts.rate, 0.7);
});

test('play again restarts from the new line', () => {
  const timers = fakeTimers();
  const sp = createSpeaker({ tts: null, synth: null, timers });
  const seen = [];
  sp.onLine((i) => seen.push(i));
  sp.play(['a', 'b', 'c'], 0, 1);
  sp.play(['a', 'b', 'c'], 2, 1);
  timers.advance(1500);
  assert.deepEqual(seen, [0, 2]);
  assert.equal(timers.pending(), 0);
});

test('synth mode speaks each line in order', () => {
  const made = [];
  class U { constructor(text) { this.text = text; made.push(this); } }
  const synth = { Utterance: U, speak() {}, cancel() {}, pause() {}, resume() {} };
  const sp = createSpeaker({ tts: null, synth, timers: fakeTimers() });
  assert.equal(sp.mode, 'synth');
  const seen = []; let ended = 0;
  sp.onLine((i) => seen.push(i)); sp.onEnd(() => ended++);
  sp.play(['a', 'b'], 0, 1.25);
  assert.equal(made[0].lang, 'ko-KR');
  assert.equal(made[0].rate, 1.25);
  made[0].onend();
  assert.deepEqual(seen, [0, 1]);
  made[1].onend();
  assert.equal(ended, 1);
});
