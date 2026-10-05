// Deck source parsing — Archidekt URL, Moxfield URL, raw decklist text.
window.DeckSrc = (() => {

  // Which deck an address names, by its parsed host (in any case, subdomains too) and path:
  // archidekt.com/decks/<id> or /api/decks/<id>, moxfield.com/decks/<publicId>. An address pasted
  // without https:// works too. A look-alike host, or a deck path on another host, is not a deck.
  function parseId(url) {
    let u;
    try {
      const text = String(url ?? '').trim();
      u = new URL(/^[a-z][a-z0-9+.-]*:\/\//i.test(text) ? text : `https://${text}`);
    } catch { return null; }
    if (u.protocol !== 'https:' && u.protocol !== 'http:') return null;
    const host = u.hostname.toLowerCase();
    const on = site => host === site || host.endsWith(`.${site}`);
    let m;
    if (on('archidekt.com') && (m = u.pathname.match(/^\/(?:api\/)?decks\/(\d+)(?:\/|$)/i))) return { kind: 'archidekt', id: m[1] };
    if (on('moxfield.com') && (m = u.pathname.match(/^\/decks\/([A-Za-z0-9_-]+)(?:\/|$)/i))) return { kind: 'moxfield', id: m[1] };
    return null;
  }

  async function fetchArchidekt(id) {
    // Fetched by the Vault server (no browser CORS problems, no third-party proxies).
    const json = await window.VaultApi.archidektDeck(id);
    // json.cards: [{ quantity, categories, card: { oracleCard: { name }, edition: { editioncode }, collectorNumber } }, ...]
    const cards = [];
    for (const c of (json.cards || [])) {
      const cats = c.categories || [];
      // skip sideboard / maybeboard
      if (cats.some(x => /sideboard|maybeboard|considering/i.test(x))) continue;
      const name = c.card?.oracleCard?.name || c.card?.name;
      const set = (c.card?.edition?.editioncode || '').toLowerCase();
      const num = c.card?.collectorNumber || '';
      const qty = c.quantity || 1;
      if (name) cards.push({ name, set, collector_number: num, qty, categories: cats });
    }
    return {
      title: json.name || `Archidekt #${id}`,
      url: `https://archidekt.com/decks/${id}`,
      author: json.owner?.username || '',
      cards,
    };
  }

  async function fetchMoxfield(id) {
    // Moxfield has no public API and asks third parties to get permission first.
    throw new Error('Moxfield decks can\'t be fetched automatically. In Moxfield use More → Export → Copy plain text, then paste it here');
    // eslint-disable-next-line no-unreachable
    const json = {};
    const boards = json.boards || {};
    const cards = [];
    for (const boardName of ['mainboard', 'commanders', 'companions']) {
      const board = boards[boardName];
      if (!board) continue;
      for (const k in (board.cards || {})) {
        const entry = board.cards[k];
        const c = entry.card;
        cards.push({
          name: c.name,
          set: (c.set || '').toLowerCase(),
          collector_number: c.cn || '',
          qty: entry.quantity || 1,
          categories: [boardName],
        });
      }
    }
    return {
      title: json.name || `Moxfield ${id}`,
      url: `https://moxfield.com/decks/${id}`,
      author: json.createdByUser?.userName || '',
      cards,
    };
  }

  // Pasted decklists are parsed on the server by mtg_toolkits.decklist, the one parser the whole
  // app uses (Archidekt / Moxfield / Arena / MTGO formats, sections, categories, *F* / *E*).
  // Only cards that are played (main deck, commander, companion) are returned.
  const PLAYED = new Set(['main', 'commander', 'companion']);
  async function parseText(text) {
    const res = await window.VaultApi.parseDeck(text);
    const cards = res.cards.filter(c => PLAYED.has(c.section));
    return { title: 'Pasted decklist', url: '', author: '', cards, unparsed: res.unparsed };
  }

  async function fetchUrl(url) {
    const parsed = parseId(url);
    if (!parsed) throw new Error('Unrecognised URL. Try Archidekt or Moxfield.');
    if (parsed.kind === 'archidekt') return await fetchArchidekt(parsed.id);
    if (parsed.kind === 'moxfield') return await fetchMoxfield(parsed.id);
  }

  // Is this an Archidekt deck's address? The same check that decides what is fetched, so the
  // "deck list from Archidekt" credit appears exactly on the decks loaded from Archidekt.
  function isArchidekt(url) {
    return parseId(url)?.kind === 'archidekt';
  }

  return { parseId, fetchUrl, parseText, fetchArchidekt, fetchMoxfield, isArchidekt };
})();
