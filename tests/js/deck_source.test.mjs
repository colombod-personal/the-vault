// public/lib/deck.js: which addresses are Archidekt decks (they get the "deck list from Archidekt" credit).
// Run: node --test tests/js/*.test.mjs   (tests/test_web_lib.py runs it with pytest)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

function load() {
  const sandbox = { URL };
  sandbox.window = sandbox;
  vm.runInNewContext(readFileSync(new URL('../../public/lib/deck.js', import.meta.url), 'utf8'), sandbox);
  return sandbox.window.DeckSrc.isArchidekt;
}

test('Archidekt deck addresses, in any case and on its subdomains', () => {
  const isArchidekt = load();
  for (const url of ['https://archidekt.com/decks/123', 'https://ARCHIDEKT.COM/decks/123/x',
                     'https://www.archidekt.com/decks/1', 'http://Archidekt.com/api/decks/9/']) {
    assert.equal(isArchidekt(url), true, url);
  }
});

test('not Archidekt: other hosts, look-alikes, a mention in the path, and junk', () => {
  const isArchidekt = load();
  for (const url of ['https://www.moxfield.com/decks/abc', 'https://evil.test/archidekt.com/decks/1',
                     'https://archidekt.com.evil.test/decks/1', 'https://notarchidekt.com/decks/1', '', 'not a url', null]) {
    assert.equal(isArchidekt(url), false, String(url));
  }
});
