import { test } from 'node:test';
import assert from 'node:assert/strict';
import { filterNotes, notesToMarkdown } from '../../src/export.js';

const notes = [
  { id: 'a', subject: '수학', unit: '이차방정식', problem: '$x^2=4$의 근', tags: ['인수분해'], stage: 0, starred: false, status: 'ready', result: { answer: 'x=±2', answer_short: '±2', concepts: [{ name: '근의 공식', summary: 's' }] } },
  { id: 'b', subject: '수학', unit: '삼각비', problem: 'sin A', tags: [], stage: 4, starred: true, status: 'ready', result: { answer: '3/5', answer_short: '3/5', concepts: [] } },
  { id: 'c', subject: '영어', unit: '관계대명사', problem: 'what vs that', tags: [], stage: 1, starred: false, status: 'failed', result: null }
];

test('filterNotes by subject, query, open, starred', () => {
  assert.deepEqual(filterNotes(notes, { subject: '영어' }).map((n) => n.id), ['c']);
  assert.deepEqual(filterNotes(notes, { query: '근의' }).map((n) => n.id), ['a']);
  assert.deepEqual(filterNotes(notes, { query: '인수분해' }).map((n) => n.id), ['a']);
  assert.deepEqual(filterNotes(notes, { query: 'SIN' }).map((n) => n.id), ['b']);
  assert.deepEqual(filterNotes(notes, { onlyOpen: true }).map((n) => n.id), ['a', 'c']);
  assert.deepEqual(filterNotes(notes, { onlyStarred: true }).map((n) => n.id), ['b']);
  assert.equal(filterNotes(notes, { subject: '전체' }).length, 3);
});

test('notesToMarkdown groups by subject and lists answers', () => {
  const md = notesToMarkdown(notes);
  assert.ok(md.startsWith('# 오답노트'));
  assert.ok(md.includes('## 수학'));
  assert.ok(md.includes('## 영어'));
  assert.ok(md.includes('- 정답: x=±2'));
  assert.ok(md.includes('- 핵심 개념: 근의 공식'));
  assert.ok(md.indexOf('## 수학') < md.indexOf('## 영어'));
});
