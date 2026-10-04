// Deck view — paste Archidekt/Moxfield URL or list, get coverage report. What you own of it and
// what the missing cards cost come from the server (POST /decks/coverage), against your collection.
const { useState: useStateD, useMemo: useMemoD, useRef: useRefD, useEffect: useEffectD } = React;

// A deck's cards as a decklist the server parses ("4 Name (SET) 123" per line).
const deckListText = (cards) => cards.map(c => `${c.qty} ${c.name}` + (c.set && c.collector_number ? ` (${c.set.toUpperCase()}) ${c.collector_number}` : '')).join('\n');

// The server's coverage line for each deck card: in order when it answers line for line, else by name.
function coverageFor(cards, lines) {
  const byName = {};
  for (const l of lines) (byName[l.name.toLowerCase().trim()] = byName[l.name.toLowerCase().trim()] || []).push(l);
  const inOrder = lines.length === cards.length && lines.every((l, i) => l.name.toLowerCase().trim() === cards[i].name.toLowerCase().trim());
  return cards.map((c, i) => (inOrder ? lines[i] : (byName[c.name.toLowerCase().trim()] || []).shift() || null));
}

function DeckView({ data, openCard, initialText }) {
  const [src, setSrc] = useStateD(initialText || '');
  const [tab, setTab] = useStateD(initialText ? 'text' : 'url'); // 'url' | 'text'
  const [saved, setSaved] = useStateD(null);
  const [myDecks, setMyDecks] = useStateD(null); // the decks you saved (null while loading)
  const refreshDecks = () => window.VaultApi.decks().then(setMyDecks).catch(() => setMyDecks([]));
  useEffectD(() => { refreshDecks(); }, []);
  const [deck, setDeck] = useStateD(null);
  const [enriched, setEnriched] = useStateD(null); // [{...deckCard, scry, owned, ownEntries}]
  const [coverage, setCoverage] = useStateD(null); // the server's deck totals
  const [loading, setLoading] = useStateD(false);
  const [error, setError] = useStateD('');
  const [progress, setProgress] = useStateD({ done: 0, total: 0 });
  const [filter, setFilter] = useStateD('all'); // 'all'|'missing'|'partial'|'owned'

  async function loadFromUrl(url = src) {
    setError(''); setDeck(null); setEnriched(null); setSaved(null); setLoading(true);
    try {
      const d = await window.DeckSrc.fetchUrl(url.trim());
      await processDeck(d);
    } catch (e) {
      setError(e.message + ' — try pasting the decklist as text instead.');
      setLoading(false);
    }
  }
  async function loadFromText(text = src) {
    setError(''); setDeck(null); setEnriched(null); setSaved(null); setLoading(true);
    try {
      const d = await window.DeckSrc.parseText(text);
      if (!d.cards.length) throw new Error('No cards parsed. Use "4 Card Name" per line.');
      await processDeck(d);
    } catch (e) {
      setError(e.message);
      setLoading(false);
    }
  }

  // A saved deck you already have (same link): saving again updates it rather than adding a copy.
  const savedMatch = deck && deck.url && myDecks ? myDecks.find((d) => d.source_url === deck.url) : null;

  async function saveDeck() {
    const text = deckListText(deck.cards);
    const name = savedMatch ? savedMatch.name
      : deck.title === 'Pasted decklist' ? (prompt('Name this deck', 'My deck') || 'My deck') : deck.title;
    try {
      setSaved(savedMatch ? await window.VaultApi.updateDeck(savedMatch.id, name, text, deck.url)
        : await window.VaultApi.saveDeck(name, text, deck.url || null));
      refreshDecks();
    } catch (e) { setError('Saving failed: ' + e.message); }
  }

  // Open a saved deck: from its link when it has one (so you see the deck as it is now), else its saved list.
  function openSaved(d) {
    if (d.source_url) { setTab('url'); setSrc(d.source_url); loadFromUrl(d.source_url); }
    else { setTab('text'); setSrc(d.text); loadFromText(d.text); }
  }
  async function removeSaved(d) {
    if (!confirm(`Remove “${d.name}” from your decks?`)) return;
    try { await window.VaultApi.deleteDeck(d.id); refreshDecks(); }
    catch (e) { setError('Removing failed: ' + e.message); }
  }

  // A deck opened from Account (saved or shared with you): analyse it straight away.
  useEffectD(() => { if (initialText) loadFromText(); }, []);

  async function processDeck(d) {
    setDeck(d);
    // What you own and what the rest costs: the server's coverage, priced in Postgres.
    const cov = await window.VaultApi.deckCoverage(deckListText(d.cards));
    const lines = coverageFor(d.cards, cov.cards);
    // Card data (image, colours, type) for display, from the Vault's card table.
    const ids = d.cards.map(c => ({ name: c.name, set: c.set, collector_number: c.collector_number }));
    setProgress({ done: 0, total: ids.length });
    const scry = await window.Scryfall.collection(ids, (p) => setProgress({ done: p.done, total: p.total }));
    const rows = d.cards.map((c, i) => {
      const line = lines[i];
      const owned = line ? line.have : 0;
      const need = line ? line.missing : c.qty;
      const unitPrice = line ? line.unit_price : null;  // no price known: unknown, not $0
      return {
        ...c,
        scry: scry[i],
        owned,
        ownEntries: (line ? line.owned_printings : []).map(o => ({ s: o.set, sn: '', cn: o.collector_number, p: o.printing, q: o.quantity, mk: o.unit_price })),
        need,
        status: line ? line.status : 'missing',
        unitPrice,
        priced: unitPrice != null,
        rowCost: line && line.missing_cost != null ? line.missing_cost : 0,
      };
    });
    setCoverage(cov);
    setEnriched(rows);
    setLoading(false);
  }

  const summary = useMemoD(() => {
    if (!enriched || !coverage) return null;
    // Tallies of the server's lines; the cost to complete is the server's.
    let total = 0, ownedQty = 0, missingQty = 0;
    let ownedFully = 0, ownedPartial = 0, missingAll = 0;
    for (const r of enriched) {
      total += r.qty;
      ownedQty += Math.min(r.owned, r.qty);
      missingQty += r.need;
      if (r.status === 'owned') ownedFully++;
      else if (r.status === 'partial') ownedPartial++;
      else missingAll++;
    }
    return { total, ownedQty, missingQty, missingCost: coverage.missing_cost || 0, unpricedQty: coverage.missing_unpriced || 0,
      ownedFully, ownedPartial, missingAll };
  }, [enriched, coverage]);

  const rowsFiltered = useMemoD(() => {
    if (!enriched) return [];
    return enriched.filter(r => filter === 'all' || r.status === filter);
  }, [enriched, filter]);

  return (
    <div data-screen-label="04 Decks">
      <div style={{ marginBottom: 24 }}>
        <p className="eyebrow">Deck coverage</p>
        <h1 className="h1" style={{ marginTop: 6 }}>What's in your vault — and what's missing.</h1>
      </div>

      <div className="coverage-grid">
        <div>
          {/* Your saved decks: open one again, from its link when it has one */}
          <div className="panel" style={{ marginBottom: 16 }}>
            <p className="eyebrow" style={{ marginBottom: 12 }}>Your decks</p>
            {myDecks === null ? <p className="muted label-mono">Loading…</p> :
             myDecks.length === 0 ? <p className="muted" style={{ fontSize: 12, lineHeight: 1.5 }}>Decks you save appear here, so you can open them again any time. Load one below and press “Save to your decks”.</p> :
             <div style={{ display: 'grid', gap: 8 }}>
               {myDecks.map((d) => (
                 <div key={d.id} style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                   <div style={{ flex: '1 1 160px', minWidth: 0 }}>
                     <div style={{ fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{d.name}</div>
                     {d.source_url ?
                       <a href={d.source_url} target="_blank" rel="noopener noreferrer" className="muted" style={{ fontSize: 11, fontFamily: 'var(--mono)', display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{d.source_url.replace(/^https?:\/\/(www\.)?/, '')}</a> :
                       <span className="muted" style={{ fontSize: 11, fontFamily: 'var(--mono)' }}>pasted list</span>}
                   </div>
                   <button className="btn sm primary" onClick={() => openSaved(d)} disabled={loading}>Open</button>
                   <button className="btn sm ghost" onClick={() => removeSaved(d)} aria-label={`Remove ${d.name}`}>Remove</button>
                 </div>
               ))}
             </div>}
          </div>

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
                  placeholder="Paste an Archidekt or Moxfield link"
                  value={src}
                  onChange={e => setSrc(e.target.value)}
                />
                <p className="muted" style={{ fontSize: 11, marginTop: 8, fontFamily: 'var(--mono)', lineHeight: 1.5 }}>
                  Archidekt decks are fetched by the Vault server.<br />For Moxfield, use the <em>Paste list</em> tab.
                </p>
                <div className="row" style={{ marginTop: 12 }}>
                  <button className="btn primary" onClick={() => loadFromUrl()} disabled={loading || !src.trim()}>
                    {loading ? <><span className="spinner"></span> Loading</> : 'Analyse deck'}
                  </button>
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
                  <button className="btn primary" onClick={() => loadFromText()} disabled={loading || !src.trim()}>
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
                  Fetching card data {progress.done}/{progress.total}…
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
                {saved ? 'Saved to your decks ✓' : savedMatch ? 'Update in your decks' : 'Save to your decks'}
              </button>

              <div style={{ marginTop: 20 }}>
                <CoverageDonut summary={summary} />
              </div>

              <div className="divider"></div>

              <div className="m-stack" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
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
                  <span style={{ fontSize: 20, color: 'var(--text-2)', position: 'relative', top: -8 }}>{summary.unpricedQty ? '≥ $' : '$'}</span>
                  {summary.missingCost.toFixed(2)}
                </p>
                <p className="muted" style={{ fontSize: 11, fontFamily: 'var(--mono)', marginTop: 6 }}>
                  at current Scryfall USD prices, checked against your collection
                </p>
                {summary.unpricedQty > 0 && (
                  <p className="muted deck-unpriced-note" style={{ fontSize: 11, fontFamily: 'var(--mono)', marginTop: 4 }}>
                    incomplete: {summary.unpricedQty} missing {summary.unpricedQty === 1 ? 'card has' : 'cards have'} no price (?) and {summary.unpricedQty === 1 ? 'is' : 'are'} not counted
                  </p>
                )}
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
                const status = r.status;
                return (
                  <div className={`deck-row ${status}`} key={i} {...(r.scry ? window.vaultPressable(() => openCard({ n: r.name, s: r.scry.set, cn: r.scry.collector_number, p: 'Normal', c: 'Mint', l: 'English', q: r.owned, mk: r.unitPrice || 0, lo: 0, mi: 0, pd: 0, fd: '', ld: '', _scry: r.scry, _ownEntries: r.ownEntries, _deckRow: r }), r.name) : {})} style={{ cursor: r.scry ? 'pointer' : 'default' }}>
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
                    <div className="cost muted">{r.need > 0 ? `${r.need} × ${r.priced ? '$' + r.unitPrice.toFixed(2) : '?'}` : '—'}</div>
                    <div className="cost" style={{ color: r.rowCost > 0 ? 'var(--gold)' : 'var(--muted)' }} title={r.need > 0 && !r.priced ? 'No price available' : undefined}>
                      {r.need > 0 ? (r.priced ? `$${r.rowCost.toFixed(2)}` : '?') : '✓'}
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
