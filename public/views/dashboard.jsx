// Dashboard view — Vault overview
const { useMemo, useState } = React;

function Dashboard({ data, gotoBrowse, gotoSet, gotoValuation, openCard, onRefresh, refreshing, refreshProgress, refreshError }) {
  const m = data.meta;
  const pnl = m.totalMarket - m.totalPaid;
  const pnlPct = m.totalPaid ? (pnl / m.totalPaid) * 100 : 0;
  const fresh = window.vaultFreshness(m.generatedAt);

  const topCards = useMemo(() => {
    return data.cards.slice()
      .sort((a, b) => (b.mk * b.q) - (a.mk * a.q))
      .slice(0, 12);
  }, [data]);

  const topSets = useMemo(() => data.sets.slice(0, 10), [data]);
  const maxSetVal = topSets[0]?.value || 1;

  // Timeline
  const timeline = data.timeline;

  // Printing donut data
  const totalPrintings = Object.values(m.printings).reduce((a, b) => a + b, 0);
  const printingRows = Object.entries(m.printings)
    .filter(([k]) => k)
    .sort((a, b) => b[1] - a[1])
    .map(([k, v]) => ({ label: k, qty: v, pct: (v / totalPrintings) * 100 }));

  const recent = useMemo(() => {
    return data.cards.slice()
      .sort((a, b) => (b.ld || '').localeCompare(a.ld || ''))
      .slice(0, 6);
  }, [data]);

  return (
    <div data-screen-label="01 Vault">
      <div style={{ display: 'flex', alignItems: 'end', justifyContent: 'space-between', marginBottom: 28 }}>
        <div>
          <p className="eyebrow">The Vault — overview</p>
          <h1 className="h1" style={{ marginTop: 6 }}>Your collection, at a glance.</h1>
        </div>
        <div style={{ textAlign: 'right' }}>
          <p className="label-mono">Generated</p>
          <p style={{ fontFamily: 'var(--mono)', fontSize: 12, color: 'var(--text-2)', marginTop: 4 }}>
            {new Date(m.generatedAt).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })}
          </p>
          {window.RefreshButton && onRefresh && (
            <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 6 }}>
              <RefreshButton refreshing={refreshing} refreshProgress={refreshProgress} onRefresh={onRefresh} />
              {refreshing && refreshProgress && (
                <div className="refresh-bar" style={{ width: 140 }}>
                  <div style={{ width: `${refreshProgress.total ? (refreshProgress.done / refreshProgress.total) * 100 : 0}%` }}></div>
                </div>
              )}
              {!refreshing && refreshError && (
                <span style={{ fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--danger)', maxWidth: 200, textAlign: 'right', lineHeight: 1.5 }}>{refreshError}</span>
              )}
            </div>
          )}
        </div>
      </div>

      <div className="stat-grid">
        <div className="stat stat-link" onClick={gotoValuation} role="button" tabIndex={0}
          onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); gotoValuation(); } }}>
          <div className="label" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <span>Market value</span>
            <span className="stat-link-cue">History →</span>
          </div>
          <div className="value"><span className="currency">$</span>{m.totalMarket.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 })}</div>
          <div className="delta">market price across {m.totalQty.toLocaleString()} cards</div>
          <div className="delta" style={{ fontSize: 10 }}>
            {m.pricedFromScryfall
              ? `prices: Scryfall (TCGplayer) for ${m.pricedFromScryfall.toLocaleString()}` +
                (m.pricedFromScryfall < m.totalQty ? `, your Dragon Shield export for the rest` : '')
              : 'prices: from your Dragon Shield export (Scryfall prices arrive with the daily update)'}
          </div>
          <div className={`freshness ${fresh.tone}`} title={`Prices calculated ${fresh.abs}`}>
            <span className="dot"></span>
            <span>{fresh.rel}</span>
            <span className="sep">·</span>
            <span className="asof">as of {fresh.abs}</span>
          </div>
        </div>
        <div className="stat accent">
          <div className="label">Total spent</div>
          {m.costsHidden ? (
            <>
              <div className="value">Private</div>
              <div className="delta">not shared by the owner</div>
            </>
          ) : (
            <>
              <div className="value"><span className="currency">$</span>{m.totalPaid.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 })}</div>
              <div className="delta">avg ${(m.totalPaid / m.totalQty).toFixed(2)} / card</div>
            </>
          )}
        </div>
        <div className={`stat ${pnl >= 0 ? 'good' : 'bad'}`} style={{ display: window.__vault?.showPnL === false || m.costsHidden ? 'none' : undefined }}>
          <div className="label">Unrealised P&amp;L</div>
          <div className="value" style={{ color: pnl >= 0 ? 'var(--good)' : 'var(--danger)' }}>
            <span className="currency">$</span>{pnl >= 0 ? '+' : '−'}{Math.abs(pnl).toLocaleString(undefined, { maximumFractionDigits: 0 })}
          </div>
          <div className={`delta ${pnl >= 0 ? 'up' : 'down'}`}>{pnl >= 0 ? '▲' : '▼'} {Math.abs(pnlPct).toFixed(1)}% on cost basis</div>
        </div>
        <div className="stat">
          <div className="label">Breadth</div>
          <div className="value">{m.uniqueSets}<span style={{ fontSize: 18, color: 'var(--muted)', marginLeft: 6 }}>sets</span></div>
          <div className="delta">{data.cards.length.toLocaleString()} unique printings</div>
        </div>
      </div>

      {/* Most valuable cards */}
      <div className="section">
        <div className="section-head">
          <div>
            <p className="eyebrow">The crown jewels</p>
            <h2 className="h2" style={{ marginTop: 4 }}>Top printings by total value</h2>
          </div>
          <button className="btn ghost" onClick={() => gotoBrowse({ sort: 'value' })}>
            Browse all →
          </button>
        </div>
        <CardGrid cards={topCards} onClick={openCard} />
      </div>

      {/* Sets ranking + Acquisition timeline */}
      <div className="col-2 section">
        <div className="panel">
          <div className="section-head" style={{ marginBottom: 8 }}>
            <div>
              <p className="eyebrow">Composition</p>
              <h2 className="h2" style={{ marginTop: 4, fontSize: 22 }}>Top sets by market value</h2>
            </div>
            <button className="btn ghost sm" onClick={() => gotoBrowse({ view: 'sets' })}>All sets →</button>
          </div>
          <div>
            {topSets.map(s => (
              <div className="bar-row" key={s.code} onClick={() => gotoSet(s.code)} style={{ cursor: 'pointer' }}>
                <div className="code" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  {window.SetIcon && <SetIcon code={s.code} size={14} />}
                  <span>{s.code}</span>
                </div>
                <div className="name">{s.name}</div>
                <div className="val">${s.value.toLocaleString(undefined, { maximumFractionDigits: 0 })}</div>
                <div className="qty">{s.qty} cards</div>
                <div className="bar"><div style={{ width: `${(s.value / maxSetVal) * 100}%` }}></div></div>
              </div>
            ))}
          </div>
        </div>

        <div className="panel">
          <p className="eyebrow">Cadence</p>
          <h2 className="h2" style={{ marginTop: 4, fontSize: 22, marginBottom: 14 }}>Acquisition timeline</h2>
          <Timeline data={timeline} />
          <div className="divider"></div>
          <p className="eyebrow" style={{ marginBottom: 10 }}>Printings</p>
          {printingRows.map(p => (
            <div key={p.label} style={{ marginBottom: 8 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, marginBottom: 4 }}>
                <span>{p.label}</span>
                <span style={{ fontFamily: 'var(--mono)', color: 'var(--muted)' }}>{p.qty.toLocaleString()} · {p.pct.toFixed(1)}%</span>
              </div>
              <div className="progress-bar"><div style={{ width: `${p.pct}%` }}></div></div>
            </div>
          ))}
        </div>
      </div>

      <div className="section">
        <div className="section-head">
          <div>
            <p className="eyebrow">Latest pulls</p>
            <h2 className="h2" style={{ marginTop: 4 }}>Most recent acquisitions</h2>
          </div>
        </div>
        <CardGrid cards={recent} onClick={openCard} />
      </div>
    </div>
  );
}

function Timeline({ data }) {
  if (!data || !data.length) return <p className="muted">No timeline data.</p>;
  // Build monthly buckets, build path
  const w = 600, h = 140, pad = 20;
  const maxQ = Math.max(...data.map(d => d.qty));
  const step = (w - pad * 2) / Math.max(data.length - 1, 1);
  const pts = data.map((d, i) => [pad + i * step, h - pad - (d.qty / maxQ) * (h - pad * 2)]);
  const path = pts.map((p, i) => (i === 0 ? `M${p[0]},${p[1]}` : `L${p[0]},${p[1]}`)).join(' ');
  const area = `${path} L${pts[pts.length-1][0]},${h-pad} L${pad},${h-pad} Z`;

  // Pick first / last / max month for labels
  const maxIdx = data.reduce((m, d, i) => d.qty > data[m].qty ? i : m, 0);
  const labels = [
    { idx: 0, text: data[0].month },
    { idx: maxIdx, text: `${data[maxIdx].qty} in ${data[maxIdx].month}` },
    { idx: data.length - 1, text: data[data.length - 1].month },
  ];
  return (
    <svg className="timeline-svg" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none">
      <defs>
        <linearGradient id="tlg" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="oklch(0.78 0.13 80)" stopOpacity="0.45" />
          <stop offset="100%" stopColor="oklch(0.78 0.13 80)" stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={area} fill="url(#tlg)" />
      <path d={path} fill="none" stroke="oklch(0.78 0.13 80)" strokeWidth="1.5" />
      {labels.map((l, i) => (
        <g key={i}>
          <circle cx={pts[l.idx][0]} cy={pts[l.idx][1]} r="3" fill="oklch(0.86 0.12 85)" />
          <text x={pts[l.idx][0]} y={pts[l.idx][1] - 10} fontSize="9" fill="var(--muted)" textAnchor="middle" fontFamily="var(--mono)">{l.text}</text>
        </g>
      ))}
    </svg>
  );
}

function CardGrid({ cards, onClick }) {
  return (
    <div className="card-grid">
      {cards.map((c, i) => <CardTile key={i} c={c} onClick={() => onClick(c)} />)}
    </div>
  );
}

function CardTile({ c, onClick }) {
  const [enriched, setEnriched] = useState(() => window.Scryfall.cached(c.n, c.s, c.cn));
  React.useEffect(() => {
    if (enriched) return;
    let stop = false;
    // Lazy-fetch ONLY on intersection? For top-12 we just fetch.
    window.Scryfall.collection([{ name: c.n, set: c.s, collector_number: c.cn }]).then(arr => {
      if (!stop && arr[0]) setEnriched(arr[0]);
    });
    return () => { stop = true; };
  }, [c.n, c.s, c.cn]);

  const totalVal = (c.mk * c.q).toFixed(2);
  return (
    <div className="card-tile" onClick={onClick}>
      <div className="img-wrap">
        {enriched && enriched.img_normal ? (
          <img src={enriched.img_normal} alt={c.n} loading="lazy" />
        ) : (
          <div className="placeholder">
            {c.n}<br/><span style={{ color: 'var(--gold)' }}>{c.s}</span> · #{c.cn}
          </div>
        )}
        {c.p && c.p.includes('Foil') ? <span className="badge foil">{c.p === 'Foil' ? 'FOIL' : c.p.toUpperCase()}</span> : null}
        {c.q > 1 ? <span className="badge" style={{ left: 8, top: 8, right: 'auto' }}>×{c.q}</span> : null}
      </div>
      <div className="meta">
        <div className="name">{c.n}</div>
        <div className="sub">
          <span>{c.s} · #{c.cn}</span>
          <span className="val">${totalVal}</span>
        </div>
      </div>
    </div>
  );
}

window.Dashboard = Dashboard;
window.CardGrid = CardGrid;
window.CardTile = CardTile;
