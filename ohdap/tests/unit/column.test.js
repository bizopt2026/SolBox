import { test } from 'node:test';
import assert from 'node:assert/strict';
import { isValidColumn, columnModel } from '../../src/column.js';

const v = { type: 'column', op: '-', a: '503', b: '267', marks: { h: '4', t: '9', o: '13' }, focus: ['o'], result: '__6' };

test('columnModel aligns digits and marks', () => {
  const m = columnModel(v);
  assert.equal(m.width, 3);
  assert.equal(m.op, '-');
  assert.deepEqual(m.rows.a, ['5', '0', '3']);
  assert.deepEqual(m.rows.b, ['2', '6', '7']);
  assert.deepEqual(m.rows.marks, ['4', '9', '13']);
  assert.deepEqual(m.rows.result, ['', '', '6']);
  assert.deepEqual(m.rows.focus, [false, false, true]);
});

test('different lengths right-align', () => {
  const m = columnModel({ type: 'column', op: '+', a: '1205', b: '98', marks: {}, focus: [], result: '' });
  assert.equal(m.width, 4);
  assert.deepEqual(m.rows.b, ['', '', '9', '8']);
  assert.deepEqual(m.rows.result, ['', '', '', '']);
});

test('isValidColumn', () => {
  assert.equal(isValidColumn(v), true);
  assert.equal(isValidColumn({ ...v, op: '/' }), false);
  assert.equal(isValidColumn({ ...v, a: '5a3' }), false);
  assert.equal(isValidColumn({ ...v, result: '12345' }), false);
  assert.equal(isValidColumn(null), false);
  assert.equal(isValidColumn({ ...v, type: 'other' }), false);
});
