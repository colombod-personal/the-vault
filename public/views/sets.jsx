// Sets view — all sets owned + drill-in
const { useEffect: useEffectS, useMemo: useMemoS, useState: useStateS } = React;

function Sets({ data, onSetClick }) {
  const [q, setQ] = useStateS('');
  const [sort, setSort] = useStateS('value');
  // Release dates come from the set list, which may arrive after this view: re-sort when it does.
  const [setsLoaded, setSetsLoaded] = useStateS(0);
  useEffectS(() => window.SetIcons?.onLoad(() => setSetsLoaded((n) => n + 1)), []);

  const sets = useMemoS(() => {
    let out = data.sets;
    if (q) {
      const ql = q.toLowerCase();
      out = out.filter(s => s.code.toLowerCase().includes(ql) || s.name.toLowerCase().includes(ql));
    }
    const released = (code) => window.SetIcons?.get(code)?.released || '';
    switch (sort) {
      case 'value': out = out.slice().sort((a, b) => b.value - a.value); break;
      case 'qty': out = out.slice().sort((a, b) => b.qty - a.qty); break;
      case 'unique': out = out.slice().sort((a, b) => b.unique - a.unique); break;
      case 'code': out = out.slice().sort((a, b) => a.code.localeCompare(b.code)); break;
      case 'name': out = out.slice().sort((a, b) => a.name.localeCompare(b.name)); break;
      case 'newest': out = out.slice().sort((a, b) => (released(b.code) || '0').localeCompare(released(a.code) || '0')); break;
      case 'oldest': out = out.slice().sort((a, b) => (released(a.code) || '9').localeCompare(released(b.code) || '9')); break;
    }
    return out;
  }, [data, q, sort, setsLoaded]);

  // At least $1, so sets all worth $0 (e.g. before the first price sync) give empty bars, not NaN%.
  const maxVal = Math.max(1, ...data.sets.map(s => s.value));

  return (
    <div data-screen-label="03 Sets">
      <div style={{ marginBottom: 24 }}>
        <p className="eyebrow">Holdings by expansion</p>
        <h1 className="h1" style={{ marginTop: 6 }}>{data.sets.length} sets in the vault.</h1>
      </div>

      <div className="panel" style={{ marginBottom: 16, padding: 16 }}>
        <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 12 }}>
          <input className="input" placeholder="Search sets…" value={q} onChange={e => setQ(e.target.value)} />
          <select className="select" value={sort} onChange={e => setSort(e.target.value)}>
            <option value="value">Sort: total value</option>
            <option value="qty">Sort: card count</option>
            <option value="unique">Sort: unique printings</option>
            <option value="newest">Sort: release date — newest first</option>
            <option value="oldest">Sort: release date — oldest first</option>
            <option value="code">Sort: set code</option>
            <option value="name">Sort: set name</option>
          </select>
        </div>
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
  const set = data.sets.find(s => s.code === code);
  const cards = useMemoS(() => data.cards.filter(c => c.s === code).sort((a, b) => (b.mk * b.q) - (a.mk * a.q)), [data, code]);
  if (!set) return null;
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
        <CardGrid cards={cards.slice(0, 60)} onClick={openCard} />
        {cards.length > 60 && <p className="muted" style={{ textAlign: 'center', marginTop: 16 }}>+{cards.length - 60} more</p>}
      </div>
    </div>
  );
}

window.Sets = Sets;
window.SetDetail = SetDetail;
