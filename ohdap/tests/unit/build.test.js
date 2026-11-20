import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const html = readFileSync(fileURLToPath(new URL('../../index.html', import.meta.url)), 'utf8');

function appScript(src) {
  const m = src.match(/<script id="app-js">([\s\S]*?)<\/script>/);
  assert.ok(m, 'app script tag present');
  return m[1];
}

test('built page is skeleton-free content with title first (publish adds the shell)', () => {
  assert.ok(!/<!doctype|<html[\s>]|<head>|<body[\s>]/.test(html), "no document shell tags");
  assert.ok(html.trimStart().startsWith('<title>오답노트</title>'));
  assert.ok(html.slice(0, 600).includes('claude:viewport-fit-sides'));
});

test('app never calls confirm/alert/prompt', () => {
  const code = html.match(/<script id="app-js">([\s\S]*?)<\/script>/)[1];
  assert.ok(!/\b(window\.)?(confirm|alert|prompt)\(/.test(code));
});

test('KaTeX is inlined with woff2 fonts as data URIs', () => {
  assert.ok(html.includes('katex'));
  assert.ok(html.includes('@font-face'));
  assert.ok(html.includes('data:font/woff2;base64,'));
  assert.ok(!/url\(fonts\//.test(html), 'no relative font urls left');
});

test('no template placeholders remain', () => {
  assert.ok(!html.includes('<!--APP_JS-->'));
  assert.ok(!html.includes('<!--KATEX_CSS-->'));
  assert.ok(!html.includes('<!--KATEX_JS-->'));
});

test('app script has no module syntax and parses', () => {
  const code = appScript(html);
  assert.ok(!/^\s*import\s/m.test(code));
  assert.ok(!/^\s*export\s/m.test(code));
  assert.doesNotThrow(() => new Function(code));
});
