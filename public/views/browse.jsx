// Browse view — searchable / filterable table of all printings
const { useMemo: useMemoB, useState: useStateB } = React;

function Browse({ data, openCard, initialQuery }) {
  const [q, setQ] = useStateB(initialQuery?.search || '');
  const [setF, setSetF] = useStateB('');
  const [printingF, setPrintingF] = useStateB('');
  const [sort, setSort] = useStateB(initialQuery?.sort || 'value');
  const [limit, setLimit] = useStateB(60);
  const [layout, setLayout] = useStateB('table');

  const filtered = useMemoB(() => {
    let out = data.cards;
    if (q) {
      const ql = q.toLowerCase();
      out = out.filter(c => c.n.toLowerCase().includes(ql) || c.s.toLowerCase().includes(ql));
    }
    if (setF) out = out.filter(c => c.s === setF);
    if (printingF) out = out.filter(c => c.p === printingF);
    switch (sort) {
      case 'value': out = out.slice().sort((a, b) => (b.mk * b.q) - (a.mk * a.q)); break;
      case 'qty': out = out.slice().sort((a, b) => b.q - a.q); break;
      case 'name': out = out.slice().sort((a, b) => a.n.localeCompare(b.n)); break;
      case 'recent': out = out.slice().sort((a, b) => (b.ld || '').localeCompare(a.ld || '')); break;
      case 'oldest': out = out.slice().sort((a, b) => (a.fd || '').localeCompare(b.fd || '')); break;
    }
    return out;
  }, [data, q, setF, printingF, sort]);

  const totalShownValue = useMemoB(() => filtered.slice(0, limit).reduce((s, c) => s + c.mk * c.q, 0), [filtered, limit]);

  const setOptions = useMemoB(() => data.sets.slice().sort((a, b) => a.code.localeCompare(b.code)), [data]);

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
            placeholder="Search by card name or set code…"
            value={q}
            onChange={e => { setQ(e.target.value); setLimit(60); }}
          />
          <select className="select" value={setF} onChange={e => { setSetF(e.target.value); setLimit(60); }}>
            <option value="">All sets ({data.sets.length})</option>
            {setOptions.map(s => <option key={s.code} value={s.code}>{s.code} — {s.name}</option>)}
          </select>
          <select className="select" value={printingF} onChange={e => setPrintingF(e.target.value)}>
            <option value="">All printings</option>
            {Object.keys(data.meta.printings).filter(k => k).map(p => <option key={p} value={p}>{p}</option>)}
          </select>
          <select className="select" value={sort} onChange={e => setSort(e.target.value)}>
            <option value="value">Sort: total value ↓</option>
            <option value="qty">Sort: quantity ↓</option>
            <option value="name">Sort: name A→Z</option>
            <option value="recent">Sort: newest first</option>
            <option value="oldest">Sort: oldest first</option>
          </select>
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 14, fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--muted)' }}>
          <div>
            <span style={{ color: 'var(--gold)' }}>{filtered.length.toLocaleString()}</span> entries match · showing top <span style={{ color: 'var(--text)' }}>{Math.min(limit, filtered.length)}</span> · combined value of shown <span style={{ color: 'var(--gold)' }}>${totalShownValue.toFixed(2)}</span>
          </div>
          <div style={{ display: 'flex', gap: 6 }}>
            <button className={`chip ${layout === 'table' ? 'active' : ''}`} onClick={() => setLayout('table')}>Table</button>
            <button className={`chip ${layout === 'grid' ? 'active' : ''}`} onClick={() => setLayout('grid')}>Grid</button>
          </div>
        </div>
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
              {filtered.slice(0, limit).map((c, i) => {
                const total = c.mk * c.q;
                const pnl = total - c.pd;
                return (
                  <tr key={i} onClick={() => openCard(c)} style={{ cursor: 'pointer' }}>
                    <td style={{ fontWeight: 600 }}>{c.n}</td>
                    <td>
                      <span className="chip" style={{ padding: '2px 8px', fontSize: 10, gap: 4 }}>
                        {window.SetIcon && <SetIcon code={c.s} size={12} />}
                        <span>{c.s}</span>
                      </span>
                    </td>
                    <td className="muted" style={{ fontFamily: 'var(--mono)', fontSize: 11 }}>{c.cn}</td>
                    <td className="muted" style={{ fontSize: 11 }}>{c.p}{c.c !== 'Mint' ? ` · ${c.c}` : ''}</td>
                    <td className="num">{c.q}</td>
                    <td className="num">${c.mk.toFixed(2)}</td>
                    <td className="num" style={{ color: 'var(--gold)' }}>${total.toFixed(2)}</td>
                    <td className="num muted">${c.pd.toFixed(2)}</td>
                    <td className="num" style={{ color: pnl >= 0 ? 'var(--good)' : 'var(--danger)' }}>{pnl >= 0 ? '+' : '−'}${Math.abs(pnl).toFixed(2)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <CardGrid cards={filtered.slice(0, limit)} onClick={openCard} />
      )}

      {limit < filtered.length && (
        <div style={{ textAlign: 'center', marginTop: 20 }}>
          <button className="btn" onClick={() => setLimit(limit + 60)}>
            Load {Math.min(60, filtered.length - limit)} more →
          </button>
          <button className="btn ghost" style={{ marginLeft: 8 }} onClick={() => setLimit(filtered.length)}>
            Show all {filtered.length.toLocaleString()}
          </button>
        </div>
      )}
    </div>
  );
}

window.Browse = Browse;
