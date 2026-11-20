import { test } from 'node:test';
import assert from 'node:assert/strict';
import { INTERVALS, MASTERED, addDays, todayYmd, initialSchedule, applyResult, isDue, dueNotes, stageLabel } from '../../src/review.js';

test('constants', () => {
  assert.deepEqual(INTERVALS, [1, 3, 7, 14]);
  assert.equal(MASTERED, 4);
});

test('addDays crosses month and year', () => {
  assert.equal(addDays('2026-12-31', 1), '2027-01-01');
  assert.equal(addDays('2026-02-27', 3), '2026-03-02');
});

test('todayYmd uses local date', () => {
  assert.equal(todayYmd(new Date(2026, 10, 20, 23, 59)), '2026-11-20');
});

test('new note is due tomorrow at stage 0', () => {
  assert.deepEqual(initialSchedule('2026-11-20'), { stage: 0, due: '2026-11-21' });
});

test('correct answers climb the ladder to mastered', () => {
  assert.deepEqual(applyResult({ stage: 0 }, true, '2026-11-21'), { stage: 1, due: '2026-11-24' });
  assert.deepEqual(applyResult({ stage: 1 }, true, '2026-11-24'), { stage: 2, due: '2026-12-01' });
  assert.deepEqual(applyResult({ stage: 2 }, true, '2026-12-01'), { stage: 3, due: '2026-12-15' });
  assert.deepEqual(applyResult({ stage: 3 }, true, '2026-12-15'), { stage: 4, due: null });
});

test('a wrong answer resets to stage 0, due tomorrow', () => {
  assert.deepEqual(applyResult({ stage: 3 }, false, '2026-11-21'), { stage: 0, due: '2026-11-22' });
  assert.deepEqual(applyResult({ stage: 4 }, false, '2026-11-21'), { stage: 0, due: '2026-11-22' });
});

test('isDue', () => {
  assert.equal(isDue({ stage: 1, due: '2026-11-20' }, '2026-11-21'), true);
  assert.equal(isDue({ stage: 1, due: '2026-11-21' }, '2026-11-21'), true);
  assert.equal(isDue({ stage: 1, due: '2026-11-22' }, '2026-11-21'), false);
  assert.equal(isDue({ stage: 4, due: null }, '2026-11-21'), false);
});

test('dueNotes sorts by due ascending', () => {
  const notes = [{ id: 'a', stage: 0, due: '2026-11-21' }, { id: 'b', stage: 1, due: '2026-11-19' }, { id: 'c', stage: 0, due: '2026-11-30' }];
  assert.deepEqual(dueNotes(notes, '2026-11-21').map((n) => n.id), ['b', 'a']);
});

test('stageLabel', () => {
  assert.equal(stageLabel({ stage: 0, due: '2026-11-20' }, '2026-11-20'), '오늘 복습 · 1단계');
  assert.equal(stageLabel({ stage: 0, due: '2026-11-18' }, '2026-11-20'), '오늘 복습 · 1단계');
  assert.equal(stageLabel({ stage: 1, due: '2026-11-23' }, '2026-11-20'), '복습 2단계 · 3일 뒤');
  assert.equal(stageLabel({ stage: 4, due: null }, '2026-11-20'), '정복');
});
