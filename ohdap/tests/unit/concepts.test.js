import { test } from 'node:test';
import assert from 'node:assert/strict';
import { groupConcepts } from '../../src/concepts.js';

const n = (id, names) => ({ id, result: { concepts: names.map((name) => ({ name })) } });

test('groupConcepts merges names ignoring spaces and case, most notes first', () => {
  const g = groupConcepts([n('a', ['근의 공식']), n('b', ['근의공식 ', '인수분해']), n('c', ['Relative Pronoun']), n('d', ['relative pronoun']), n('e', ['근의 공식'])]);
  assert.equal(g[0].name, '근의 공식');
  assert.deepEqual(g[0].notes.map((x) => x.id), ['a', 'b', 'e']);
  assert.equal(g[1].notes.length, 2);
  assert.equal(g.length, 3);
});

test('notes without results are ignored and a note counts once per concept', () => {
  const g = groupConcepts([{ id: 'x', result: null }, n('a', ['A', 'a'])]);
  assert.equal(g.length, 1);
  assert.equal(g[0].notes.length, 1);
});
