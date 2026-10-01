// Set icons (Scryfall's), with the set list from the Vault (/api/v1/catalog/sets). Cached in localStorage for a day,
// as the server caches it. Exposes window.SetIcons.get(code) → { name, icon, released, type } | null, where code is a
// Scryfall set code or a Dragon Shield one (e.g. GK2_ORZHOV).
(() => {
  const CACHE_KEY = 'scry_sets_v3';
  const MAX_AGE_MS = 24 * 3600 * 1000;
  let bySymbol = {};
  let aliases = {};
  let prefixes = [];
  let savedAt = 0;
  let inflight = null;
  const listeners = new Set();

  try {
    const saved = JSON.parse(localStorage.getItem(CACHE_KEY) || 'null');
    if (saved && typeof saved.at === 'number' && saved.sets) {
      bySymbol = saved.sets;
      aliases = saved.aliases || {};
      prefixes = saved.prefixes || [];
      savedAt = saved.at;
    }
  } catch {}

  const fresh = () => Object.keys(bySymbol).length > 0 && Date.now() - savedAt < MAX_AGE_MS;

  async function loadAll() {
    if (fresh()) return bySymbol;
    if (inflight) return inflight;
    inflight = (async () => {
      try {
        const sets = [];
        let names = {};
        let pre = [];
        for (let url = '/api/v1/catalog/sets?limit=500'; url; ) {  // pages of at most 500
          const r = await fetch(url, { credentials: 'same-origin' });
          if (!r.ok) throw new Error('Set list ' + r.status);
          const j = await r.json();
          sets.push(...(j.items || []));
          if (j.aliases) names = j.aliases;
          if (j.alias_prefixes) pre = j.alias_prefixes;
          url = j._links && j._links.next ? j._links.next.href : null;
        }
        const out = {};
        for (const s of sets) {
          out[s.code.toLowerCase()] = {
            name: s.name,
            icon: s.icon_svg_uri,
            released: s.released_at,
            type: s.set_type,
          };
        }
        bySymbol = out;
        aliases = names;
        prefixes = pre;
        savedAt = Date.now();
        try { localStorage.setItem(CACHE_KEY, JSON.stringify({ at: savedAt, sets: out, aliases: names, prefixes: pre })); } catch {}
        for (const fn of listeners) fn();
        return out;
      } finally {
        inflight = null;
      }
    })();
    return inflight;
  }

  function get(code) {
    if (!code) return null;
    // The library's rule (mtg_toolkits.normalize_set_code): exact aliases, then the prefixes.
    const c = code.trim().toLowerCase();
    const alias = aliases[c] || (prefixes.some((p) => c.startsWith(p)) ? c.slice(0, 3) : null);
    return bySymbol[c] || (alias && bySymbol[alias]) || null;
  }
  function onLoad(fn) { listeners.add(fn); return () => listeners.delete(fn); }

  // Kick off background load (a stale copy keeps serving until the new list arrives)
  loadAll().catch(() => {});

  window.SetIcons = { get, loadAll, onLoad };
})();
