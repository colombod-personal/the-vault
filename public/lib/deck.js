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

  // Raw decklist text parser. Supports:
  //  "4 Lightning Bolt"
  //  "4x Lightning Bolt"
  //  "4 Lightning Bolt (M11) 149"
  //  "1 Sol Ring [CMR]"
  //  "// Sideboard" headers (skipped — only mainboard)
  function parseText(text) {
    const lines = text.split(/\r?\n/);
    let inSideboard = false;
    const cards = [];
    for (let raw of lines) {
      const line = raw.trim();
      if (!line) continue;
      if (/^(\/\/|#)\s*(sideboard|maybeboard|considering)/i.test(line)) { inSideboard = true; continue; }
      if (/^(\/\/|#)\s*(mainboard|deck|commander)/i.test(line)) { inSideboard = false; continue; }
      if (/^(SIDEBOARD|MAYBEBOARD):?$/i.test(line)) { inSideboard = true; continue; }
      if (inSideboard) continue;
      // Match "[qty]x? Name (SET) num"
      const m = line.match(/^(\d+)x?\s+(.+?)(?:\s+[\(\[]([A-Za-z0-9_]+)[\)\]])?(?:\s+(\S+))?$/i);
      if (!m) continue;
      const qty = parseInt(m[1]);
      const name = m[2].trim();
      const set = (m[3] || '').toLowerCase();
      const num = m[4] || '';
      if (!name) continue;
      cards.push({ name, set, collector_number: num, qty });
    }
    return { title: 'Pasted decklist', url: '', author: '', cards };
  }

  async function fetchUrl(url) {
    const parsed = parseId(url);
    if (!parsed) throw new Error('Unrecognised URL. Try Archidekt or Moxfield.');
    if (parsed.kind === 'archidekt') return await fetchArchidekt(parsed.id);
    if (parsed.kind === 'moxfield') return await fetchMoxfield(parsed.id);
  }

  return { parseId, fetchUrl, parseText, fetchArchidekt, fetchMoxfield };
})();
