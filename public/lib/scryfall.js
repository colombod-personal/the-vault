// Card data (Scryfall's). Printings you own come with their card data from the collection's pages
// (kept in Postgres by the daily sync). Anything else (deck cards, a card shown without it) is
// asked of the Vault: POST /api/v1/cards/lookup answers from the server's copy and asks Scryfall
// itself for anything missing, so the browser never calls Scryfall's API. Prices are refreshed on
// the server too (POST /api/v1/collection/refresh, VaultApi.refreshCollection). Images still load from Scryfall's image CDN, as Scryfall asks.
// Calls stay 500 ms apart (the server's Scryfall limit). Lookups are kept in memory only.
window.Scryfall = (() => {
  const LOOKUP = '/api/v1/cards/lookup';
  const RATE_MS = 500;
  let lastCall = 0;

  // Older versions cached card data in localStorage (scry_cache, _v2, _v3): drop it.
  try {
    for (let i = localStorage.length - 1; i >= 0; i--) {
      const k = localStorage.key(i);
      if (k && k.startsWith('scry_cache')) localStorage.removeItem(k);
    }
  } catch {}

  let cache = {};  // lookups made on this page
  function setCache(key, val) { cache[key] = val; }

  // Callers take turns through one promise chain, so concurrent lookups stay 500 ms apart.
  let turns = Promise.resolve();
  function rateLimit() {
    const turn = turns.then(async () => {
      const wait = Math.max(0, RATE_MS - (Date.now() - lastCall));
      if (wait) await new Promise(r => setTimeout(r, wait));
      lastCall = Date.now();
    });
    turns = turn.catch(() => {});
    return turn;
  }

  function slimCard(c) {
    if (!c) return null;
    // Strip to essentials
    const img = c.image_uris || c.card_faces?.[0]?.image_uris;
    return {
      id: c.id,
      name: c.name,
      set: c.set,
      set_name: c.set_name,
      collector_number: c.collector_number,
      colors: c.colors || c.card_faces?.[0]?.colors || [],
      color_identity: c.color_identity || [],
      type_line: c.type_line || c.card_faces?.[0]?.type_line || '',
      mana_cost: c.mana_cost || c.card_faces?.[0]?.mana_cost || '',
      cmc: c.cmc,
      rarity: c.rarity,
      img_small: img?.small || null,
      img_normal: img?.normal || null,
      img_art: img?.art_crop || null,
      prices: c.prices || {},
      scryfall_uri: c.scryfall_uri,
      artist: c.artist || c.card_faces?.[0]?.artist || null,
      oracle_text: c.oracle_text || c.card_faces?.map(f => f.oracle_text).join(' // ') || '',
      power: c.power, toughness: c.toughness, loyalty: c.loyalty,
      layout: c.layout,
    };
  }

  function cacheKey(name, set, num) {
    if (set && num) return `${set}/${num}`.toLowerCase();
    return `n:${name.toLowerCase().trim()}`;
  }

  // Look up many cards. ids: [{name, set?, collector_number?}, ...]
  // Returns Promise<Array<slimCard|null>> in same order
  async function collection(ids, onProgress) {
    const results = new Array(ids.length).fill(null);
    const toFetch = [];
    ids.forEach((id, idx) => {
      const k = cacheKey(id.name, id.set, id.collector_number);
      const known = cache[k];
      if (known) results[idx] = known;
      else toFetch.push({ idx, id, k });
    });

    if (onProgress) onProgress({ done: ids.length - toFetch.length, total: ids.length });

    // Batch in 75s
    let netErrors = 0; // batches that never came back (network / blocked / rate-limited)
    for (let i = 0; i < toFetch.length; i += 75) {
      const batch = toFetch.slice(i, i + 75);
      const body = {
        identifiers: batch.map(b => {
          if (b.id.set && b.id.collector_number) {
            return { set: b.id.set.toLowerCase(), collector_number: String(b.id.collector_number) };
          }
          return { name: b.id.name };
        }),
      };
      // Up to 3 attempts with backoff; handles transient 429/5xx and flaky networks.
      let json = null, lastErr = null;
      for (let attempt = 0; attempt < 3 && !json; attempt++) {
        await rateLimit();
        if (attempt > 0) await new Promise(r => setTimeout(r, 400 * attempt));
        try {
          const resp = await fetch(LOOKUP, {
            method: 'POST', credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify(body),
          });
          if (resp.status === 429) { lastErr = 'HTTP 429'; await new Promise(r => setTimeout(r, 30000)); continue; }
          if (resp.status >= 500) { lastErr = 'HTTP ' + resp.status; continue; }
          if (!resp.ok) { lastErr = 'HTTP ' + resp.status; break; }
          json = await resp.json();
          if (json.unavailable && !(json.data || []).length) { lastErr = 'Scryfall unavailable'; json = null; continue; }
        } catch (e) {
          lastErr = e.message; // network error / CORS / blocked — retry
        }
      }
      if (!json) {
        netErrors++;
        if (onProgress) onProgress({ done: (ids.length - toFetch.length) + i + batch.length, total: ids.length, error: lastErr });
        continue;
      }
      const found = json.data || [];
      // Match each batch row to its identifier (Scryfall returns in order of resolved cards, w/ not_found separately)
      // Build a map by name lowercase for fallback lookup
      const foundByName = new Map();
      const foundBySetNum = new Map();
      for (const c of found) {
        const slim = slimCard(c);
        foundByName.set(c.name.toLowerCase(), slim);
        // Double-faced and split cards ("Fire // Ice") are also asked for by one face's name.
        for (const face of c.name.split(' // ')) {
          if (!foundByName.has(face.toLowerCase())) foundByName.set(face.toLowerCase(), slim);
        }
        foundBySetNum.set(`${c.set}/${c.collector_number}`, slim);
      }
      for (const b of batch) {
        let slim = null;
        if (b.id.set && b.id.collector_number) {
          slim = foundBySetNum.get(`${b.id.set.toLowerCase()}/${b.id.collector_number}`) || foundByName.get(b.id.name.toLowerCase()) || null;
        } else {
          slim = foundByName.get(b.id.name.toLowerCase()) || null;
        }
        if (slim) {
          setCache(b.k, slim);
          results[b.idx] = slim;
        } else {
          // Cache the miss as null with short TTL? Just don't cache.
        }
      }
      if (onProgress) onProgress({ done: (ids.length - toFetch.length) + i + batch.length, total: ids.length });
    }
    results.netErrors = netErrors;
    results.batches = Math.ceil(toFetch.length / 75);
    return results;
  }

  async function named(name) {
    const k = cacheKey(name);
    if (cache[k]) return cache[k];
    await rateLimit();
    try {
      const r = await fetch(LOOKUP, {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
        body: JSON.stringify({ identifiers: [{ name }] }),
      });
      if (!r.ok) return null;
      const j = (await r.json()).data[0];
      if (!j) return null;
      const s = slimCard(j);
      setCache(k, s);
      return s;
    } catch { return null; }
  }

  function cached(name, set, num) {
    const k = cacheKey(name, set, num);
    return cache[k] || null;
  }

  return { collection, named, cached };
})();
