import { test } from 'node:test';
import assert from 'node:assert/strict';
import { GRADES, isElementary, gradeLabel, words } from '../../src/grades.js';

test('grade list runs 초3..고3', () => {
  assert.equal(GRADES.length, 10);
  assert.equal(GRADES[0], '초3');
  assert.equal(GRADES[9], '고3');
});

test('isElementary', () => {
  assert.equal(isElementary('초6'), true);
  assert.equal(isElementary('초3'), true);
  assert.equal(isElementary('중1'), false);
  assert.equal(isElementary('auto'), false);
});

test('gradeLabel', () => {
  assert.equal(gradeLabel('auto'), '자동');
  assert.equal(gradeLabel('중2'), '중2');
});

test('words switch by grade', () => {
  assert.equal(words('초3').board, '선생님 설명 듣기');
  assert.equal(words('중2').board, '판서로 풀이 보기');
  assert.equal(words('초4').concepts, '꼭 기억해요');
  assert.equal(words('고1').concepts, '핵심 개념');
  assert.equal(words('초3').drill, '비슷한 문제 도전!');
  assert.equal(words('auto').review, '복습');
  assert.equal(words('초5').review, '다시 풀기');
  assert.equal(words('초3').why, '어디서 틀렸을까?');
  assert.equal(words('중1').why, '왜 틀렸을까?');
  for (const k of ['concepts','solutions','board','review','drill','mistakes','why','myAnswer','answer','upload','wrongMsg','rightMsg']) {
    assert.ok(words('초3')[k] && words('고3')[k], k);
  }
});
