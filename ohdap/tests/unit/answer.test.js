import { test } from 'node:test';
import assert from 'node:assert/strict';
import { normalizeAnswer, quickGrade } from '../../src/answer.js';

const right = [
  ['-1', '−1'], ['x=-1', '-1'], [' 236 ', '236'], ['0.5', '1/2'], ['③', '3'], ['3', '③'],
  ['-1,3', '3, -1'], ['2.', '2'], ['１２', '12'], ['x = -1 또는 3', '-1, 3']
];
for (const [u, a] of right) test(`right: ${u} vs ${a}`, () => assert.equal(quickGrade(u, a), 'right'));

test('wrong numeric answers', () => {
  assert.equal(quickGrade('346', '236'), 'wrong');
  assert.equal(quickGrade('3', '-1, 3'), 'wrong');
});

test('empty answer is wrong', () => assert.equal(quickGrade('  ', '2'), 'wrong'));

test('non-numeric mismatch is unsure', () => {
  assert.equal(quickGrade('2x+1', '1+2x'), 'unsure');
  assert.equal(quickGrade('관계대명사 what', 'what'), 'unsure');
});

test('identical text is right', () => assert.equal(quickGrade('What', 'what'), 'right'));

test('normalizeAnswer', () => {
  assert.equal(normalizeAnswer(' x = −1. '), '-1');
  assert.equal(normalizeAnswer('①'), '1');
});

test('words containing 과/와 are not split', () => {
  assert.equal(normalizeAnswer('사과'), '사과');
  assert.equal(normalizeAnswer('-1과 3'), '-1,3');
});
