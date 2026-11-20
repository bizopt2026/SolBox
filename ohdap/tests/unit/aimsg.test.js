import { test } from 'node:test';
import assert from 'node:assert/strict';
import { aiErrorText } from '../../src/ui/aimsg.js';

// Final review fix 6: only real permission problems talk about permission.
test('error wording matches the code', () => {
  assert.ok(aiErrorText({ code: 'not_granted' }).includes('허용'));
  for (const code of ['upstream_error', 'image_rejected', 'images_unavailable', 'refused', 'prompt_too_large', 'session_expired', 'bad_output']) {
    assert.ok(!aiErrorText({ code }).includes('허용'), code);
  }
  assert.ok(aiErrorText({ code: 'image_rejected' }).includes('사진'));
  assert.ok(aiErrorText({ code: 'session_expired' }).includes('로그인'));
});
