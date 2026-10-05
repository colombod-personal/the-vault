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

function parser() {
  const sandbox = { URL };
  sandbox.window = sandbox;
  vm.runInNewContext(readFileSync(new URL('../../public/lib/deck.js', import.meta.url), 'utf8'), sandbox);
  return sandbox.window.DeckSrc.parseId;
}

test('Archidekt deck addresses, in any case and on its subdomains', () => {
  const isArchidekt = load();
  for (const url of ['https://archidekt.com/decks/123', 'https://ARCHIDEKT.COM/decks/123/x',
                     'https://www.archidekt.com/decks/1', 'http://Archidekt.com/api/decks/9/']) {
    assert.equal(isArchidekt(url), true, url);
  }
});

test('not an Archidekt deck: other Archidekt pages, other hosts, look-alikes, a mention in the path, and junk', () => {
  const isArchidekt = load();
  for (const url of ['https://archidekt.com/terms', 'https://archidekt.com/', 'https://archidekt.com/decks',
                     'https://archidekt.com/decks/abc', 'https://archidekt.com/forum/thread/40353',
                     'https://www.moxfield.com/decks/abc', 'https://evil.test/archidekt.com/decks/1',
                     'https://archidekt.com.evil.test/decks/1', 'https://notarchidekt.com/decks/1', '', 'not a url', null]) {
    assert.equal(isArchidekt(url), false, String(url));
  }
});

test('what is loaded: Archidekt and Moxfield decks by their host, with or without https://', () => {
  const parseId = parser();
  const cases = [['https://archidekt.com/decks/123/my-deck', 'archidekt', '123'], ['archidekt.com/decks/7', 'archidekt', '7'],
                 ['https://ARCHIDEKT.COM/api/decks/9/', 'archidekt', '9'], ['https://www.moxfield.com/decks/aB_c-1', 'moxfield', 'aB_c-1'],
                 ['  moxfield.com/decks/xyz  ', 'moxfield', 'xyz']];
  for (const [url, kind, id] of cases) {
    const got = parseId(url);
    assert.equal(got?.kind, kind, url);
    assert.equal(got?.id, id, url);
  }
});

test('nothing is loaded from a look-alike or a deck path on another host', () => {
  const parseId = parser();
  for (const url of ['https://evil.test/archidekt.com/decks/1', 'https://archidekt.com.evil.test/decks/1',
                     'https://notarchidekt.com/decks/1', 'https://evil.test/moxfield.com/decks/abc',
                     'https://archidekt.com/terms', 'https://moxfield.com/', 'ftp://archidekt.com/decks/1', '', 'not a url', null]) {
    assert.equal(parseId(url), null, String(url));
  }
});

test('a saved deck matches the deck you open only through the same host check', () => {
  const sandbox = { URL };
  sandbox.window = sandbox;
  vm.runInNewContext(readFileSync(new URL('../../public/lib/deck.js', import.meta.url), 'utf8'), sandbox);
  const { sourceKey } = sandbox.window.DeckSrc;
  const real = sourceKey('https://archidekt.com/decks/1/my-deck');
  assert.equal(real, 'archidekt:1');
  for (const same of ['https://ARCHIDEKT.COM/api/decks/1/', 'archidekt.com/decks/1', 'https://www.archidekt.com/decks/1']) {
    assert.equal(sourceKey(same), real, same);
  }
  for (const lookalike of ['https://evil.test/archidekt.com/decks/1', 'https://archidekt.com.evil.test/decks/1',
                           'https://notarchidekt.com/decks/1']) {
    assert.notEqual(sourceKey(lookalike), real, lookalike);
  }
  assert.equal(sourceKey('https://moxfield.com/decks/aB_1'), 'moxfield:aB_1');
  assert.equal(sourceKey(''), null);
  assert.equal(sourceKey(null), null);
});
