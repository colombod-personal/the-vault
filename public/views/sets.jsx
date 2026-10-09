// Sets view — all sets owned + drill-in. The server searches, sorts and values the sets
// (GET /collection/sets) and lists a set's printings (GET /collection/cards?set=).
const { useEffect: useEffectS, useState: useStateS } = React;

// The view's sort choices, as the server's `sort` parameter.
const SET_SORTS = { value: '-value', qty: '-quantity', unique: '-unique', newest: '-release', oldest: 'release',
  code: 'code', name: 'name' };

function Sets({ data, onSetClick }) {
  const api = data.api;
  const [q, setQ] = useStateS('');
  const [query, setQuery] = useStateS('');  // sent to the server a moment after typing stops
  const [sort, setSort] = useStateS('value');
  useEffectS(() => {
    const t = setTimeout(() => setQuery(q), 250);
    return () => clearTimeout(t);
  }, [q]);

  const res = window.useVaultQuery(() => api.sets({ q: query, sort: SET_SORTS[sort] }), [api.base, data.meta.version, query, sort]);
  const sets = res.data ? res.data.items : [];

  // At least $1, so sets all worth $0 (e.g. before the first price sync) give empty bars, not NaN%.
  const maxVal = Math.max(1, ...sets.map(s => s.value));

  return (
    <div data-screen-label="03 Sets">
      <div style={{ marginBottom: 24 }}>
        <p className="eyebrow">Holdings by expansion</p>
        <h1 className="h1" style={{ marginTop: 6 }}>{data.meta.uniqueSets} sets in {window.scopeActive(data.scope) ? 'this selection' : 'the vault'}.</h1>
      </div>

      <div className="panel" style={{ marginBottom: 16, padding: 16 }}>
        <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 12 }}>
          <input className="input" placeholder="Search sets…" aria-label="Search sets" value={q} onChange={e => setQ(e.target.value)} />
          <select className="select" aria-label="Sort sets by" value={sort} onChange={e => setSort(e.target.value)}>
            <option value="value">Sort: total value</option>
            <option value="qty">Sort: card count</option>
            <option value="unique">Sort: unique printings</option>
            <option value="newest">Sort: release date — newest first</option>
            <option value="oldest">Sort: release date — oldest first</option>
            <option value="code">Sort: set code</option>
            <option value="name">Sort: set name</option>
          </select>
        </div>
        {res.error && (
          <p role="alert" style={{ marginTop: 10, fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--danger)' }}>
            Couldn't load the sets: {res.error.message}
          </p>
        )}
      </div>

      <div className="set-grid">
        {sets.map(s => (
          <div key={s.code} className="set-tile" style={{ '--pct': `${(s.value / maxVal) * 100}%` }} {...window.vaultPressable(() => onSetClick(s.code), `${s.name} (${s.code})`)}>
            <div className="code-row">
              <span className="code" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                {window.SetIcon && <SetIcon code={s.code} size={18} fallback={false} />}
                <span>{s.code}</span>
              </span>
              <span className="qty">{s.qty} cards</span>
            </div>
            <div className="name">{s.name}</div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginTop: 'auto' }}>
              <span className="val">${s.value.toLocaleString(undefined, { maximumFractionDigits: 0 })}</span>
              <span className="qty">{s.unique} unique</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function SetDetail({ data, code, onBack, openCard }) {
  const api = data.api;
  const at = [api.base, data.meta.version, code];
  const sets = window.useVaultQuery(() => api.sets({ q: code }), at).data;
  const set = sets && sets.items.find(s => s.code === code);
  const page = window.useVaultQuery(() => api.cards({ set: code, sort: '-value', limit: 60 }), at).data;
  if (!sets) return null;
  if (!set) {  // a set with no copies in the selection (the bucket or tag was changed on this page)
    return (
      <div data-screen-label="03 Set Detail">
        <button className="btn ghost" onClick={onBack} style={{ marginBottom: 16 }}>← Back to all sets</button>
        <div className="panel scope-empty" role="status">
          <h2 className="h2">No copies of {code} here.</h2>
          <p className="muted">{window.scopeActive(data.scope) ? 'None of the cards in this selection is from that set.' : 'You own no cards from that set.'}</p>
        </div>
      </div>
    );
  }
  const cards = page ? page.items : [];
  const more = page ? page.total - cards.length : 0;
  return (
    <div data-screen-label="03 Set Detail">
      <button className="btn ghost" onClick={onBack} style={{ marginBottom: 16 }}>← Back to all sets</button>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 16, marginBottom: 8 }}>
        {window.SetIcon && <SetIcon code={set.code} size={32} fallback={false} />}
        <span style={{ fontFamily: 'var(--mono)', fontSize: 24, color: 'var(--gold)', letterSpacing: '0.06em' }}>{set.code}</span>
        <h1 className="h1" style={{ fontSize: 34 }}>{set.name}</h1>
      </div>
      <p className="muted" style={{ fontFamily: 'var(--mono)', fontSize: 12 }}>
        {set.qty} cards · {set.unique} unique printings · ${set.value.toLocaleString()} total market value
      </p>
      <div style={{ marginTop: 24 }}>
        <CardGrid cards={cards} onClick={openCard} />
        {more > 0 && <p className="muted" style={{ textAlign: 'center', marginTop: 16 }}>+{more} more</p>}
      </div>
    </div>
  );
}

window.Sets = Sets;
window.SetDetail = SetDetail;
