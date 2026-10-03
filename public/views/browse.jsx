// Browse view — searchable / filterable table of all printings. The server filters, sorts, pages
// and totals (GET /collection/cards); this view shows its pages.
const { useEffect: useEffectB, useState: useStateB } = React;

const BROWSE_PAGE = 60;
// The view's sort choices, as the server's `sort` parameter.
const BROWSE_SORTS = { value: '-value', qty: '-quantity', name: 'name', recent: '-acquired', oldest: 'acquired' };

function Browse({ data, openCard, initialQuery }) {
  const api = data.api;
  const [q, setQ] = useStateB(initialQuery?.search || '');
  const [query, setQuery] = useStateB(q);  // the search sent to the server, a moment after typing stops
  const [setF, setSetF] = useStateB('');
  const [printingF, setPrintingF] = useStateB('');
  const [sort, setSort] = useStateB(initialQuery?.sort || 'value');
  const [layout, setLayout] = useStateB('table');
  const [shown, setShown] = useStateB(null);   // { items, total, value_total, more }
  const [busy, setBusy] = useStateB(false);
  const [error, setError] = useStateB(null);

  useEffectB(() => {
    const t = setTimeout(() => setQuery(q), 250);
    return () => clearTimeout(t);
  }, [q]);

  const params = { q: query, set: setF, printing: printingF, sort: BROWSE_SORTS[sort] || '-value' };
  const first = window.useVaultQuery(() => api.cards({ ...params, limit: BROWSE_PAGE }),
    [api.base, data.meta.version, query, setF, printingF, sort]);
  useEffectB(() => { if (first.data) { setShown(first.data); setError(null); } }, [first.data]);
  useEffectB(() => { if (first.error) setError(first.error.message); }, [first.error]);

  // Next pages follow the server's `next` links; "Show all" asks again in pages of 500.
  async function load(next) {
    setBusy(true);
    try {
      let page = await next(), items = [...shown.items, ...page.items];
      if (next === shown.more) {
        setShown({ ...page, items });
        return;
      }
      items = page.items;
      while (page.more) {
        page = await page.more();
        items = items.concat(page.items);
      }
      setShown({ ...page, items });
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  const showAll = () => load(() => api.cards({ ...params, limit: 500 }));

  const sets = (window.useVaultQuery(() => api.sets({ sort: 'code' }), [api.base, data.meta.version]).data || { items: [] }).items;
  const items = shown ? shown.items : [];
  const total = shown ? shown.total : 0;
  const hidden = data.meta.costsHidden;

  return (
    <div data-screen-label="02 Browse">
      <div style={{ marginBottom: 24 }}>
        <p className="eyebrow">Library</p>
        <h1 className="h1" style={{ marginTop: 6 }}>Every printing you own.</h1>
      </div>

      <div className="panel" style={{ marginBottom: 16, padding: 16 }}>
        <div style={{ display: 'grid', gridTemplateColumns: '1.5fr 1fr 1fr 1fr', gap: 12 }}>
          <input
            className="input"
            placeholder="Search by card name or set…"
            aria-label="Search by card name or set"
            value={q}
            onChange={e => setQ(e.target.value)}
          />
          <select className="select" aria-label="Filter by set" value={setF} onChange={e => setSetF(e.target.value)}>
            <option value="">All sets ({data.meta.uniqueSets})</option>
            {sets.map(s => <option key={s.code} value={s.code}>{s.code} — {s.name}</option>)}
          </select>
          <select className="select" aria-label="Filter by printing" value={printingF} onChange={e => setPrintingF(e.target.value)}>
            <option value="">All printings</option>
            {Object.keys(data.meta.printings).filter(k => k).map(p => <option key={p} value={p}>{p}</option>)}
          </select>
          <select className="select" aria-label="Sort by" value={sort} onChange={e => setSort(e.target.value)}>
            <option value="value">Sort: total value ↓</option>
            <option value="qty">Sort: quantity ↓</option>
            <option value="name">Sort: name A→Z</option>
            <option value="recent">Sort: newest first</option>
            <option value="oldest">Sort: oldest first</option>
          </select>
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 14, fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--text-2)' }}>
          <div aria-live="polite">
            <span style={{ color: 'var(--gold)' }}>{total.toLocaleString()}</span> entries match · showing <span style={{ color: 'var(--text)' }}>{items.length.toLocaleString()}</span> · combined value <span style={{ color: 'var(--gold)' }}>${(shown?.value_total || 0).toFixed(2)}</span>
            {first.loading && <span className="muted"> · loading…</span>}
          </div>
          <div style={{ display: 'flex', gap: 6 }}>
            <button className={`chip ${layout === 'table' ? 'active' : ''}`} onClick={() => setLayout('table')}>Table</button>
            <button className={`chip ${layout === 'grid' ? 'active' : ''}`} onClick={() => setLayout('grid')}>Grid</button>
          </div>
        </div>
        {error && <p role="alert" style={{ marginTop: 10, fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--danger)' }}>Couldn't load: {error}</p>}
      </div>

      {layout === 'table' ? (
        <div className="panel panel-flush">
          <table className="tbl">
            <thead>
              <tr>
                <th>Card</th>
                <th>Set</th>
                <th>#</th>
                <th>Printing</th>
                <th className="num">Qty</th>
                <th className="num">Market</th>
                <th className="num">Total</th>
                <th className="num">Spent</th>
                <th className="num">P&amp;L</th>
              </tr>
            </thead>
            <tbody>
              {items.map((c) => (
                // shared without prices paid, or no price paid recorded: no fake $0 / full-value P&L
                <tr key={c.key} onClick={() => openCard(c)} style={{ cursor: 'pointer' }}>
                  <td style={{ fontWeight: 600 }}>
                    {/* the row is clickable with a mouse; this button makes it reachable by keyboard */}
                    <button type="button" className="row-link" onClick={(e) => { e.stopPropagation(); openCard(c); }}>{c.n}</button>
                  </td>
                  <td>
                    <span className="chip" style={{ padding: '2px 8px', fontSize: 10, gap: 4 }}>
                      {window.SetIcon && <SetIcon code={c.s} size={12} fallback={false} />}
                      <span>{c.s}</span>
                    </span>
                  </td>
                  <td className="muted" style={{ fontFamily: 'var(--mono)', fontSize: 11 }}>{c.cn}</td>
                  <td className="muted" style={{ fontSize: 11 }}>{c.p}{c.c !== 'Mint' ? ` · ${c.c}` : ''}</td>
                  <td className="num">{c.q}</td>
                  <td className="num">${c.mk.toFixed(2)}</td>
                  <td className="num" style={{ color: 'var(--gold)' }}>${c.v.toFixed(2)}</td>
                  <td className="num muted">{window.vaultSpentText(c, hidden)}</td>
                  {hidden || c.gain == null ? <td className="num muted">{window.vaultGainText(c, hidden)}</td> : (
                    <td className="num" style={{ color: c.gain >= 0 ? 'var(--good)' : 'var(--danger)' }}>{window.vaultGainText(c, hidden)}</td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <CardGrid cards={items} onClick={openCard} />
      )}

      {shown && shown.more && (
        <div style={{ textAlign: 'center', marginTop: 20 }}>
          <button className="btn" disabled={busy} onClick={() => load(shown.more)}>
            Load {Math.min(BROWSE_PAGE, total - items.length)} more →
          </button>
          <button className="btn ghost" style={{ marginLeft: 8 }} disabled={busy} onClick={showAll}>
            Show all {total.toLocaleString()}
          </button>
        </div>
      )}
    </div>
  );
}

window.Browse = Browse;
