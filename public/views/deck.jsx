// Deck view — paste Archidekt/Moxfield URL or list, get coverage report
const { useState: useStateD, useMemo: useMemoD, useRef: useRefD, useEffect: useEffectD } = React;

const SAMPLE_URL = 'https://archidekt.com/decks/5292775/the_dragon_in_the_night';

function DeckView({ data, openCard, initialText }) {
  const [src, setSrc] = useStateD(initialText || SAMPLE_URL);
  const [tab, setTab] = useStateD(initialText ? 'text' : 'url'); // 'url' | 'text'
  const [saved, setSaved] = useStateD(null);
  const [deck, setDeck] = useStateD(null);
  const [enriched, setEnriched] = useStateD(null); // [{...deckCard, scry, owned, ownEntries}]
  const [loading, setLoading] = useStateD(false);
  const [error, setError] = useStateD('');
  const [progress, setProgress] = useStateD({ done: 0, total: 0 });
  const [filter, setFilter] = useStateD('all'); // 'all'|'missing'|'partial'|'owned'

  // Index of owned cards by lowercase name
  const byName = data.byName;

  async function loadFromUrl() {
    setError(''); setDeck(null); setEnriched(null); setLoading(true);
    try {
      const d = await window.DeckSrc.fetchUrl(src.trim());
      await processDeck(d);
    } catch (e) {
      setError(e.message + ' — try pasting the decklist as text instead.');
      setLoading(false);
    }
  }
  async function loadFromText() {
    setError(''); setDeck(null); setEnriched(null); setLoading(true);
    try {
      const d = window.DeckSrc.parseText(src);
      if (!d.cards.length) throw new Error('No cards parsed. Use "4 Card Name" per line.');
      await processDeck(d);
    } catch (e) {
      setError(e.message);
      setLoading(false);
    }
  }

  async function saveDeck() {
    const text = deck.cards.map(c => `${c.qty} ${c.name}` + (c.set && c.collector_number ? ` (${c.set.toUpperCase()}) ${c.collector_number}` : '')).join('\n');
    const name = deck.title === 'Pasted decklist' ? (prompt('Name this deck', 'My deck') || 'My deck') : deck.title;
    try { setSaved(await window.VaultApi.saveDeck(name, text, deck.url || null)); }
    catch (e) { setError('Saving failed: ' + e.message); }
  }

  // A deck opened from Account (saved or shared with you): analyse it straight away.
  useEffectD(() => { if (initialText) loadFromText(); }, []);

  async function processDeck(d) {
    setDeck(d);
    // Resolve each card via Scryfall (for image + price + colors)
    const ids = d.cards.map(c => ({ name: c.name, set: c.set, collector_number: c.collector_number }));
    setProgress({ done: 0, total: ids.length });
    const scry = await window.Scryfall.collection(ids, (p) => setProgress({ done: p.done, total: p.total }));
    const rows = d.cards.map((c, i) => {
      const key = c.name.toLowerCase().trim();
      const own = byName[key];
      let owned = 0, ownEntries = [];
      if (own) {
        owned = own.total;
        ownEntries = own.entries;
      }
      const s = scry[i];
      const price = s?.prices ? (parseFloat(s.prices.usd) || parseFloat(s.prices.usd_foil) || 0) : 0;
      const need = Math.max(0, c.qty - owned);
      return {
        ...c,
        scry: s,
        owned,
        ownEntries,
        need,
        unitPrice: price,
        rowCost: need * price,
      };
    });
    setEnriched(rows);
    setLoading(false);
  }

  const summary = useMemoD(() => {
    if (!enriched) return null;
    let total = 0, ownedQty = 0, missingQty = 0, missingCost = 0;
    let ownedFully = 0, ownedPartial = 0, missingAll = 0;
    for (const r of enriched) {
      total += r.qty;
      const have = Math.min(r.owned, r.qty);
      ownedQty += have;
      missingQty += r.need;
      missingCost += r.rowCost;
      if (r.owned >= r.qty) ownedFully++;
      else if (r.owned > 0) ownedPartial++;
      else missingAll++;
    }
    return { total, ownedQty, missingQty, missingCost, ownedFully, ownedPartial, missingAll };
  }, [enriched]);

  const rowsFiltered = useMemoD(() => {
    if (!enriched) return [];
    return enriched.filter(r => {
      if (filter === 'all') return true;
      if (filter === 'missing') return r.owned === 0;
      if (filter === 'partial') return r.owned > 0 && r.owned < r.qty;
      if (filter === 'owned') return r.owned >= r.qty;
    });
  }, [enriched, filter]);

  return (
    <div data-screen-label="04 Decks">
      <div style={{ marginBottom: 24 }}>
        <p className="eyebrow">Deck coverage</p>
        <h1 className="h1" style={{ marginTop: 6 }}>What's in your vault — and what's missing.</h1>
      </div>

      <div className="coverage-grid">
        <div>
          {/* Input panel */}
          <div className="panel">
            <p className="eyebrow" style={{ marginBottom: 12 }}>Load a deck</p>
            <div className="row" style={{ gap: 4, marginBottom: 12 }}>
              <button className={`chip ${tab === 'url' ? 'active' : ''}`} onClick={() => setTab('url')}>From URL</button>
              <button className={`chip ${tab === 'text' ? 'active' : ''}`} onClick={() => setTab('text')}>Paste list</button>
            </div>
            {tab === 'url' ? (
              <>
                <input
                  className="input"
                  placeholder="archidekt.com/decks/… or moxfield.com/decks/…"
                  value={src}
                  onChange={e => setSrc(e.target.value)}
                />
                <p className="muted" style={{ fontSize: 11, marginTop: 8, fontFamily: 'var(--mono)', lineHeight: 1.5 }}>
                  Sample: <span style={{ color: 'var(--gold)' }}>The Dragon in the Night</span> is pre-loaded.<br />
                  Archidekt decks are fetched by the Vault server.<br />For Moxfield, use the <em>Paste list</em> tab.
                </p>
                <div className="row" style={{ marginTop: 12 }}>
                  <button className="btn primary" onClick={loadFromUrl} disabled={loading}>
                    {loading ? <><span className="spinner"></span> Loading</> : 'Analyse deck'}
                  </button>
                  <button className="btn ghost" onClick={() => setSrc(SAMPLE_URL)}>Sample</button>
                </div>
              </>
            ) : (
              <>
                <textarea
                  className="input"
                  rows="14"
                  placeholder={'1 Sol Ring\n1 Arcane Signet\n4 Lightning Bolt\n…\n\nFormats supported:\n4 Lightning Bolt\n1 Sol Ring (CMR) 123'}
                  value={src}
                  onChange={e => setSrc(e.target.value)}
                />
                <div className="row" style={{ marginTop: 12 }}>
                  <button className="btn primary" onClick={loadFromText} disabled={loading}>
                    {loading ? <><span className="spinner"></span> Loading</> : 'Analyse list'}
                  </button>
                </div>
              </>
            )}
            {error && (
              <div style={{ marginTop: 12, padding: 12, background: 'oklch(0.62 0.18 25 / 0.08)', border: '1px solid oklch(0.62 0.18 25 / 0.4)', borderRadius: 'var(--radius)', fontSize: 12, lineHeight: 1.5 }}>
                <strong style={{ color: 'var(--danger)' }}>Couldn't fetch:</strong> {error}
              </div>
            )}
            {loading && progress.total > 0 && (
              <div style={{ marginTop: 12 }}>
                <div className="progress-bar"><div style={{ width: `${(progress.done / progress.total) * 100}%` }}></div></div>
                <p className="muted" style={{ fontSize: 10, fontFamily: 'var(--mono)', marginTop: 6, letterSpacing: '0.12em' }}>
                  Fetching {progress.done}/{progress.total} from Scryfall…
                </p>
              </div>
            )}
          </div>

          {/* Summary panel */}
          {summary && (
            <div className="panel" style={{ marginTop: 16 }}>
              <p className="eyebrow" style={{ marginBottom: 8 }}>{deck?.title}</p>
              {deck?.author && <p className="muted" style={{ fontSize: 11, fontFamily: 'var(--mono)' }}>by {deck.author}</p>}
              {deck?.url && /archidekt\.com/.test(deck.url) && (
                <p className="muted" style={{ fontSize: 10, fontFamily: 'var(--mono)' }}>
                  Deck list from <a href={deck.url} target="_blank" rel="noopener noreferrer">Archidekt</a>. Thanks to its author.
                </p>
              )}
              <button className="btn xs" style={{ marginTop: 8 }} disabled={!!saved} onClick={saveDeck}>
                {saved ? 'Saved ✓ (share it from Account)' : 'Save deck'}
              </button>

              <div style={{ marginTop: 20 }}>
                <CoverageDonut summary={summary} />
              </div>

              <div className="divider"></div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                <div>
                  <p className="label-mono">Owned</p>
                  <p style={{ fontFamily: 'var(--display)', fontSize: 28, fontWeight: 600, lineHeight: 1, marginTop: 4, color: 'var(--good)' }}>
                    {summary.ownedQty}<span style={{ color: 'var(--muted)', fontSize: 16 }}>/{summary.total}</span>
                  </p>
                </div>
                <div>
                  <p className="label-mono">Missing</p>
                  <p style={{ fontFamily: 'var(--display)', fontSize: 28, fontWeight: 600, lineHeight: 1, marginTop: 4, color: summary.missingQty > 0 ? 'var(--danger)' : 'var(--good)' }}>
                    {summary.missingQty}
                  </p>
                </div>
              </div>

              <div style={{ marginTop: 20, padding: 14, background: 'var(--bg-2)', borderRadius: 'var(--radius)', border: '1px solid var(--border)' }}>
                <p className="label-mono">Cost to complete</p>
                <p style={{ fontFamily: 'var(--display)', fontSize: 42, fontWeight: 600, lineHeight: 1, marginTop: 4, color: 'var(--gold)' }}>
                  <span style={{ fontSize: 20, color: 'var(--text-2)', position: 'relative', top: -8 }}>$</span>
                  {summary.missingCost.toFixed(2)}
                </p>
                <p className="muted" style={{ fontSize: 11, fontFamily: 'var(--mono)', marginTop: 6 }}>
                  at current Scryfall USD prices
                </p>
              </div>

              <div className="divider"></div>

              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 8, textAlign: 'center' }}>
                <FilterPill active={filter === 'owned'} onClick={() => setFilter(filter === 'owned' ? 'all' : 'owned')} count={summary.ownedFully} label="Have" color="var(--good)" />
                <FilterPill active={filter === 'partial'} onClick={() => setFilter(filter === 'partial' ? 'all' : 'partial')} count={summary.ownedPartial} label="Partial" color="var(--gold)" />
                <FilterPill active={filter === 'missing'} onClick={() => setFilter(filter === 'missing' ? 'all' : 'missing')} count={summary.missingAll} label="Need" color="var(--danger)" />
              </div>

              {deck?.url && (
                <a href={deck.url} target="_blank" rel="noopener noreferrer" style={{ display: 'block', marginTop: 16, fontSize: 11, fontFamily: 'var(--mono)', color: 'var(--muted)', textDecoration: 'underline' }}>
                  {/archidekt\.com/.test(deck.url) ? 'View this deck on Archidekt ↗' : 'Open original deck ↗'}
                </a>
              )}
            </div>
          )}
        </div>

        {/* Right column — card list */}
        <div>
          {!enriched && !loading && (
            <div className="panel" style={{ padding: 60, textAlign: 'center' }}>
              <div style={{ fontFamily: 'var(--display)', fontSize: 42, color: 'var(--muted)', marginBottom: 12, lineHeight: 1 }}>◇</div>
              <p className="h-display" style={{ fontSize: 22, marginBottom: 8 }}>Awaiting a decklist.</p>
              <p className="muted" style={{ fontSize: 13, maxWidth: 360, margin: '0 auto', lineHeight: 1.6 }}>
                Paste an Archidekt or Moxfield URL on the left, or click <span className="kbd">Analyse deck</span> to load the sample.
              </p>
            </div>
          )}

          {enriched && (
            <div className="panel panel-flush">
              <div className="deck-row head">
                <div>Qty</div>
                <div>Card</div>
                <div className="num">Have</div>
                <div className="num">Need × $</div>
                <div className="num">Cost</div>
              </div>
              {rowsFiltered.map((r, i) => {
                const status = r.owned >= r.qty ? 'owned' : (r.owned > 0 ? 'partial' : 'missing');
                return (
                  <div className={`deck-row ${status}`} key={i} onClick={() => r.scry && openCard({ n: r.name, s: r.scry.set, cn: r.scry.collector_number, p: 'Normal', c: 'Mint', l: 'English', q: r.owned, mk: r.unitPrice, lo: 0, mi: 0, pd: 0, fd: '', ld: '', _scry: r.scry, _ownEntries: r.ownEntries, _deckRow: r })} style={{ cursor: 'pointer' }}>
                    <div className="qty">{r.qty}×</div>
                    <div>
                      <div style={{ fontWeight: 600 }}>{r.name}</div>
                      {r.scry && (
                        <div className="muted" style={{ fontSize: 10, fontFamily: 'var(--mono)', marginTop: 2, display: 'flex', gap: 6, alignItems: 'center' }}>
                          <ColorIdentity colors={r.scry.color_identity} />
                          <span>{r.scry.type_line?.split(' — ')[0]}</span>
                          <span style={{ color: 'var(--gold)' }}>· {r.scry.set?.toUpperCase()}</span>
                        </div>
                      )}
                    </div>
                    <div className={`have ${status === 'owned' ? 'full' : status === 'partial' ? 'part' : 'none'}`}>
                      {r.owned > 0 ? `${Math.min(r.owned, r.qty)}/${r.qty}` : '0'}
                    </div>
                    <div className="cost muted">{r.need > 0 ? `${r.need} × $${r.unitPrice.toFixed(2)}` : '—'}</div>
                    <div className="cost" style={{ color: r.rowCost > 0 ? 'var(--gold)' : 'var(--muted)' }}>
                      {r.rowCost > 0 ? `$${r.rowCost.toFixed(2)}` : '✓'}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function FilterPill({ active, onClick, count, label, color }) {
  return (
    <button onClick={onClick} style={{
      padding: '12px 8px',
      background: active ? `${color.replace(')', ' / 0.12)').replace('oklch(', 'oklch(')}` : 'var(--bg-2)',
      border: `1px solid ${active ? color : 'var(--border)'}`,
      borderRadius: 'var(--radius)',
      cursor: 'pointer',
      transition: 'all .15s',
    }}>
      <div style={{ fontFamily: 'var(--display)', fontSize: 22, fontWeight: 600, color, lineHeight: 1 }}>{count}</div>
      <div className="label-mono" style={{ marginTop: 4 }}>{label}</div>
    </button>
  );
}

function ColorIdentity({ colors }) {
  if (!colors || !colors.length) return <span className="pip sm C">C</span>;
  return (
    <span className="mana">
      {colors.map(c => <span key={c} className={`pip sm ${c}`}>{c}</span>)}
    </span>
  );
}

function CoverageDonut({ summary }) {
  const r = 50, c = 2 * Math.PI * r;
  const ownedPct = summary.total > 0 ? summary.ownedQty / summary.total : 0;
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
      <svg width="120" height="120" viewBox="0 0 120 120" className="donut">
        <circle cx="60" cy="60" r={r} stroke="oklch(0.32 0.014 65)" strokeWidth="10" />
        <circle
          cx="60" cy="60" r={r}
          stroke="oklch(0.66 0.14 145)"
          strokeWidth="10"
          strokeDasharray={`${c * ownedPct} ${c}`}
          strokeLinecap="butt"
        />
      </svg>
      <div>
        <div style={{ fontFamily: 'var(--display)', fontSize: 36, fontWeight: 600, lineHeight: 1 }}>
          {(ownedPct * 100).toFixed(0)}<span style={{ fontSize: 18, color: 'var(--muted)' }}>%</span>
        </div>
        <div className="label-mono" style={{ marginTop: 4 }}>Coverage</div>
      </div>
    </div>
  );
}

window.DeckView = DeckView;
window.ColorIdentity = ColorIdentity;
