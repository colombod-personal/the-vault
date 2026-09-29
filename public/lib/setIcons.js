// Set icons (Scryfall's), with the set list from the Vault (/api/v1/catalog/sets). Fetched once, cached in localStorage. Exposes
// window.SetIcons.get(code) → URL | null  and a <SetIcon code="DSK" /> helper.
(() => {
  const CACHE_KEY = 'scry_sets_v1';
  let bySymbol = {};
  let loaded = false;
  let inflight = null;
  const listeners = new Set();

  try { bySymbol = JSON.parse(localStorage.getItem(CACHE_KEY) || '{}'); }
  catch {}
  if (Object.keys(bySymbol).length > 0) loaded = true;

  async function loadAll() {
    if (loaded && Object.keys(bySymbol).length > 50) return bySymbol;
    if (inflight) return inflight;
    inflight = (async () => {
      try {
        const r = await fetch('/api/v1/catalog/sets', { credentials: 'same-origin' });
        if (!r.ok) throw new Error('Set list ' + r.status);
        const j = await r.json();
        const out = {};
        for (const s of j.items || []) {
          out[s.code.toLowerCase()] = {
            name: s.name,
            icon: s.icon_svg_uri,
            released: s.released_at,
            type: s.set_type,
          };
        }
        bySymbol = out;
        loaded = true;
        try { localStorage.setItem(CACHE_KEY, JSON.stringify(out)); } catch {}
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
    return bySymbol[code.toLowerCase()] || null;
  }
  function onLoad(fn) { listeners.add(fn); return () => listeners.delete(fn); }

  // Kick off background load
  loadAll();

  window.SetIcons = { get, loadAll, onLoad };
})();
