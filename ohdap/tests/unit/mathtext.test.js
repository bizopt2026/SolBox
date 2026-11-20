import { test } from 'node:test';
import assert from 'node:assert/strict';
import katex from 'katex';
import { splitMath, renderMathText } from '../../src/mathtext.js';

test('splitMath separates $..$ and keeps escaped dollars as text', () => {
  assert.deepEqual(splitMath('근 $x^2$ 와 \\$5'), [
    { type: 'text', value: '근 ' }, { type: 'math', value: 'x^2' }, { type: 'text', value: ' 와 $5' }
  ]);
});

test('unclosed $ stays text', () => {
  assert.deepEqual(splitMath('가격 $5'), [{ type: 'text', value: '가격 $5' }]);
});

test('renderMathText escapes text and renders math with KaTeX', () => {
  const html = renderMathText('<b>$x$', katex);
  assert.ok(html.includes('&lt;b&gt;'));
  assert.ok(html.includes('class="katex'));
});

test('bad LaTeX falls back to raw text for that piece only', () => {
  const html = renderMathText('$\\frac{1}{$ 끝 $y$', katex);
  assert.ok(html.includes('math-raw'));
  assert.ok(html.includes('끝'));
  assert.ok(html.includes('class="katex'));
});

test('without katex, math is shown raw', () => {
  const html = renderMathText('$x<1$', undefined);
  assert.ok(html.includes('<span class="math-raw">x&lt;1</span>'));
});
