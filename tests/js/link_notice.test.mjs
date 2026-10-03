// public/lib/linkNotice.js: the notice shown after "Link Google" (etc.) comes back.
// Run: node --test tests/js/*.test.mjs   (tests/test_web_lib.py runs it with pytest)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

function load() {
  const sandbox = { URLSearchParams };
  sandbox.window = sandbox;
  vm.runInNewContext(readFileSync(new URL('../../public/lib/linkNotice.js', import.meta.url), 'utf8'), sandbox);
  return sandbox.window.VaultLinkNotice.noticeFromSearch;
}

test('no link round trip, no notice', () => {
  assert.equal(load()('?invite=abc'), null);
  assert.equal(load()(''), null);
});

test('a sign-in moved from an empty account says that account was removed', () => {
  const n = load()('?linked=microsoft&empty_account=removed&invite=abc');
  assert.equal(n.tone, 'ok');
  assert.equal(n.text, 'Microsoft is now linked to this vault (its empty test account was removed).');
  assert.equal(n.params.toString(), 'invite=abc', 'only the link keys are cleaned from the URL');
});

test('a kept account and a plain link are both reported', () => {
  assert.match(load()('?linked=apple&empty_account=kept').text, /^Apple is now linked.*signed out\.$/);
  assert.equal(load()('?linked=facebook').text, 'Facebook is now linked to this vault.');
});

test('a refusal explains what to do, naming the provider', () => {
  const n = load()('?link_error=identity_in_use&provider=apple');
  assert.equal(n.tone, 'danger');
  assert.match(n.text, /^Apple already has its own Vault account with data/);
  assert.match(n.text, /sign in with Apple, download .* delete it .* link Apple\.$/);
  assert.equal(n.params.toString(), '');
});

test('unknown providers and errors still read well', () => {
  assert.match(load()('?link_error=identity_in_use').text, /^That sign-in .* sign in with it,/);
  assert.equal(load()('?link_error=server_error&provider=google').text, 'Linking failed (server_error). Please try again.');
});
