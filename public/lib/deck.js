// Deck source parsing — Archidekt URL, Moxfield URL, raw decklist text.
window.DeckSrc = (() => {

  function parseId(url) {
    // Archidekt: archidekt.com/decks/<id>/<slug>
    let m = url.match(/archidekt\.com\/(?:decks|api\/decks)\/(\d+)/i);
    if (m) return { kind: 'archidekt', id: m[1] };
    // Moxfield: moxfield.com/decks/<publicId>
    m = url.match(/moxfield\.com\/decks\/([A-Za-z0-9_-]+)/i);
    if (m) return { kind: 'moxfield', id: m[1] };
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

  // Is this an Archidekt deck's address? By its parsed host, in any case (ARCHIDEKT.COM too), and a deck
  // path (/decks/<id> or /api/decks/<id>), so the Archidekt credit appears on every Archidekt deck and never
  // on another Archidekt page or a page that only mentions archidekt.com.
  function isArchidekt(url) {
    try {
      const u = new URL(url);
      const host = u.hostname.toLowerCase();
      if (host !== 'archidekt.com' && !host.endsWith('.archidekt.com')) return false;
      return /^\/(?:api\/)?decks\/\d+(?:\/|$)/i.test(u.pathname);
    } catch { return false; }
  }

  return { parseId, fetchUrl, parseText, fetchArchidekt, fetchMoxfield, isArchidekt };
})();
