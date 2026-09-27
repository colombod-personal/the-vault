// Valuation view — collection value over time (expanded Market Value tile)
const { useMemo: useMemoVal, useState: useStateVal, useRef: useRefVal, useEffect: useEffectVal, useLayoutEffect: useLayoutEffectVal } = React;

function monthLabel(ym) {
  const [y, mo] = ym.split('-').map(Number);
  const d = new Date(y, mo - 1, 1);
  return d.toLocaleDateString(undefined, { month: 'short' }) + " '" + String(y).slice(2);
}
const fmtFull = (v) => '$' + Math.round(v).toLocaleString();
const fmtAxis = (v) => v >= 1000 ? '$' + (v / 1000).toFixed(v >= 10000 ? 0 : 1) + 'K' : '$' + Math.round(v);

function Valuation({ data, onBack, openCard, onRefresh, onBulkSync, refreshing, refreshProgress, refreshError }) {
  const m = data.meta;
  const fresh = window.vaultFreshness(m.generatedAt);

  // Build monthly cumulative series from acquisition dates, valued at current prices.
  const full = useMemoVal(() => {
    const byMonth = {};
    for (const c of data.cards) {
      const ym = (c.fd || '').slice(0, 7);
      if (!ym) continue;
      const b = byMonth[ym] || (byMonth[ym] = { market: 0, cost: 0, cards: 0 });
      b.market += (c.mk || 0) * (c.q || 0);
      b.cost += c.pd || 0;
      b.cards += c.q || 0;
    }
    const months = Object.keys(byMonth).sort();
    let mc = 0,cc = 0,qc = 0;
    return months.map((ym) => {
      mc += byMonth[ym].market;cc += byMonth[ym].cost;qc += byMonth[ym].cards;
      return {
        ym, label: monthLabel(ym),
        marketCum: mc, costCum: cc, gainCum: mc - cc, cardsCum: qc,
        marketAdd: byMonth[ym].market, costAdd: byMonth[ym].cost, cardsAdd: byMonth[ym].cards
      };
    });
  }, [data]);

  const [range, setRange] = useStateVal('all');
  const series = useMemoVal(() => {
    if (range === 'all') return full;
    const n = parseInt(range, 10);
    return full.slice(Math.max(0, full.length - n));
  }, [full, range]);

  const last = full[full.length - 1] || { marketCum: 0, costCum: 0, gainCum: 0 };
  const pnlPct = last.costCum ? last.gainCum / last.costCum * 100 : 0;

  // Biggest single month by market value added.
  const peak = useMemoVal(() => full.reduce((a, b) => b.marketAdd > a.marketAdd ? b : a, full[0] || {}), [full]);
  // Last 12 months net change in market value.
  const yoy = full.length > 12 ? last.marketCum - full[full.length - 13].marketCum : last.marketCum;

  const monthsDesc = useMemoVal(() => full.slice().reverse(), [full]); // eslint-disable-line no-unused-vars

  // Ledger filtering / sorting so the table never runs off the page.
  const [ledgerQuery, setLedgerQuery] = useStateVal('');
  const [ledgerSort, setLedgerSort] = useStateVal('recent');
  const ledgerRows = useMemoVal(() => {
    const q = ledgerQuery.trim().toLowerCase();
    let rows = q ? full.filter((r) => r.label.toLowerCase().includes(q) || r.ym.includes(q)) : full.slice();
    const sorters = {
      recent: (a, b) => b.ym.localeCompare(a.ym),
      oldest: (a, b) => a.ym.localeCompare(b.ym),
      market: (a, b) => b.marketAdd - a.marketAdd,
      spend: (a, b) => b.costAdd - a.costAdd,
      cards: (a, b) => b.cardsAdd - a.cardsAdd
    };
    return rows.sort(sorters[ledgerSort] || sorters.recent);
  }, [full, ledgerQuery, ledgerSort]);

  const bulkBusy = refreshing && refreshProgress && refreshProgress.mode === 'bulk';
  const mb = (b) => (b / 1048576).toFixed(1) + ' MB';

  return (
    <div data-screen-label="Valuation">
      <div style={{ display: 'flex', alignItems: 'end', justifyContent: 'space-between', marginBottom: 28 }}>
        <div>
          <button className="btn ghost sm" onClick={onBack} style={{ marginBottom: 14 }}>← Back to vault</button>
          <p className="eyebrow">The Vault — valuation</p>
          <h1 className="h1" style={{ marginTop: 6 }}>Collection value over time.</h1>
        </div>
        <div style={{ textAlign: 'right' }}>
          <p className="label-mono">Calculated</p>
          <div className={`freshness ${fresh.tone}`} style={{ justifyContent: 'flex-end', marginTop: 6 }} title={`Prices calculated ${fresh.abs}`}>
            <span className="dot"></span>
            <span>{fresh.rel}</span>
          </div>
          <p style={{ fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--text-2)', marginTop: 4 }}>as of {fresh.abs}</p>
          {window.RefreshButton && onRefresh && (
            <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 6 }}>
              <RefreshButton refreshing={refreshing} refreshProgress={refreshProgress} onRefresh={onRefresh} />
              {onBulkSync && (
                <button className="btn ghost xs bulk-link" disabled={refreshing} onClick={onBulkSync}
                  title="Reload the prices the server synced from Scryfall's daily file (no Scryfall calls from your browser).">
                  ↓ Load server prices
                </button>
              )}
              {bulkBusy && (
                <span style={{ fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--muted)' }}>
                  {refreshProgress.phase === 'download'
                    ? `Downloading daily file… ${mb(refreshProgress.received || 0)}${refreshProgress.total ? ' / ' + mb(refreshProgress.total) : ''}`
                    : refreshProgress.phase === 'parse' ? 'Reading prices…'
                    : 'Matching your cards…'}
                </span>
              )}
              {refreshing && refreshProgress && refreshProgress.mode !== 'bulk' && (
                <div className="refresh-bar" style={{ width: 168 }}>
                  <div style={{ width: `${refreshProgress.total ? (refreshProgress.done / refreshProgress.total) * 100 : 0}%` }}></div>
                </div>
              )}
              {refreshing && refreshProgress && refreshProgress.mode !== 'bulk' && (
                <span style={{ fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--muted)' }}>
                  {refreshProgress.done.toLocaleString()} / {refreshProgress.total.toLocaleString()} printings
                </span>
              )}
              {!refreshing && refreshError && (
                <span style={{ fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--danger)', maxWidth: 220, textAlign: 'right', lineHeight: 1.5 }}>{refreshError}</span>
              )}
            </div>
          )}
        </div>
      </div>

      <div className="stat-grid" style={{ marginBottom: 16 }}>
        <div className="stat">
          <div className="label">Market value</div>
          <div className="value"><span className="currency">$</span>{Math.round(last.marketCum).toLocaleString()}</div>
          <div className="delta">at prices as of {fresh.abs}</div>
        </div>
        <div className="stat accent">
          <div className="label">Cost basis</div>
          <div className="value"><span className="currency">$</span>{Math.round(last.costCum).toLocaleString()}</div>
          <div className="delta">cumulative spend</div>
        </div>
        <div className={`stat ${last.gainCum >= 0 ? 'good' : 'bad'}`}>
          <div className="label">Unrealised gain</div>
          <div className="value" style={{ color: last.gainCum >= 0 ? 'var(--good)' : 'var(--danger)' }}>
            <span className="currency">$</span>{last.gainCum >= 0 ? '+' : '−'}{Math.abs(Math.round(last.gainCum)).toLocaleString()}
          </div>
          <div className={`delta ${last.gainCum >= 0 ? 'up' : 'down'}`}>{last.gainCum >= 0 ? '▲' : '▼'} {Math.abs(pnlPct).toFixed(1)}% over cost</div>
        </div>
        <div className="stat">
          <div className="label">Last 12 months</div>
          <div className="value" style={{ color: 'var(--gold)' }}><span className="currency">$</span>+{Math.round(yoy).toLocaleString()}</div>
          <div className="delta">value added since {full.length > 12 ? full[full.length - 13].label : full[0]?.label}</div>
        </div>
      </div>

      <div className="panel" style={{ marginBottom: 24 }}>
        <div className="section-head" style={{ marginBottom: 18 }}>
          <div>
            <p className="eyebrow">Growth curve</p>
            <h2 className="h2" style={{ marginTop: 4, fontSize: 22 }}>Cumulative value vs. cost basis</h2>
          </div>
          <div style={{ display: 'flex', gap: 6 }}>
            {[['all', 'All'], ['24', '24M'], ['12', '12M']].map(([k, lbl]) =>
            <button key={k} className={`chip ${range === k ? 'active' : ''}`} onClick={() => setRange(k)}>{lbl}</button>
            )}
          </div>
        </div>

        <div style={{ display: 'flex', gap: 20, marginBottom: 14, flexWrap: 'wrap' }}>
          <Legend swatch="gold" label="Market value (at current prices)" />
          <Legend swatch="copper" label="Cost basis (what you paid)" />
          <Legend swatch="band" label="Unrealised gain" />
        </div>

        <ValueChart series={series} />

        <p className="muted" style={{ fontSize: 11, fontFamily: 'var(--mono)', lineHeight: 1.6, marginTop: 16, maxWidth: 760 }}>
          Each point is your holdings as of that month. The cost line is what you actually paid over
          time; the market line values those same holdings at the latest prices (calculated {fresh.abs}).
          The gold band between them is unrealised gain — it is not a record of past market prices, which
          aren't tracked here.
        </p>
      </div>

      <div className="section">
        <div className="section-head" style={{ marginBottom: 8, alignItems: 'flex-end', flexWrap: 'wrap', gap: 12 }}>
          <div>
            <p className="eyebrow">Ledger</p>
            <h2 className="h2" style={{ marginTop: 4, fontSize: 22 }}>Value added by month</h2>
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <input
              className="input"
              type="text"
              value={ledgerQuery}
              onChange={(e) => setLedgerQuery(e.target.value)}
              placeholder="Filter month — e.g. 2024, Jan"
              style={{ width: 200, fontSize: 12 }}
            />
            <select className="select" value={ledgerSort} onChange={(e) => setLedgerSort(e.target.value)} style={{ fontSize: 12 }}>
              <option value="recent">Newest first</option>
              <option value="oldest">Oldest first</option>
              <option value="market">Most value added</option>
              <option value="spend">Most spent</option>
              <option value="cards">Most cards</option>
            </select>
          </div>
        </div>
        <div className="panel panel-flush">
          <div className="ledger-scroll">
            <table className="tbl ledger-tbl">
              <thead>
                <tr>
                  <th>Month</th>
                  <th className="num">Cards added</th>
                  <th className="num">Spend</th>
                  <th className="num">Market value added</th>
                  <th className="num">Cumulative value</th>
                </tr>
              </thead>
              <tbody>
                {ledgerRows.map((r) =>
                <tr key={r.ym}>
                    <td>{r.label}</td>
                    <td className="num muted">{r.cardsAdd.toLocaleString()}</td>
                    <td className="num muted">${Math.round(r.costAdd).toLocaleString()}</td>
                    <td className="num" style={{ color: 'var(--gold)' }}>${Math.round(r.marketAdd).toLocaleString()}</td>
                    <td className="num">${Math.round(r.marketCum).toLocaleString()}</td>
                  </tr>
                )}
                {ledgerRows.length === 0 &&
                <tr><td colSpan={5} className="muted" style={{ textAlign: 'center', padding: 24 }}>No months match “{ledgerQuery}”.</td></tr>
                }
              </tbody>
            </table>
          </div>
          <div className="ledger-foot">
            Showing {ledgerRows.length} of {full.length} months
          </div>
        </div>
      </div>
    </div>);

}

function Legend({ swatch, label }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <span className={`legend-sw ${swatch}`}></span>
      <span style={{ fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--text-2)' }}>{label}</span>
    </div>);

}

function ValueChart({ series }) {
  const wrapRef = useRefVal(null);
  const [w, setW] = useStateVal(900);
  const [hover, setHover] = useStateVal(null);

  useLayoutEffectVal(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      for (const e of entries) setW(Math.max(320, e.contentRect.width));
    });
    ro.observe(el);
    setW(Math.max(320, el.clientWidth));
    return () => ro.disconnect();
  }, []);

  const H = 360,padL = 58,padR = 18,padT = 16,padB = 30;
  const innerW = w - padL - padR,innerH = H - padT - padB;
  const n = series.length;
  const maxY = Math.max(1, ...series.map((d) => d.marketCum));
  const x = (i) => padL + (n <= 1 ? innerW / 2 : i / (n - 1) * innerW);
  const y = (v) => padT + innerH - v / maxY * innerH;
  const baseY = y(0);

  const lineFor = (key) => series.map((d, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(d[key]).toFixed(1)}`).join(' ');
  const marketLine = lineFor('marketCum');
  const costLine = lineFor('costCum');
  const costArea = n ? `M${x(0).toFixed(1)},${baseY} ${series.map((d, i) => `L${x(i).toFixed(1)},${y(d.costCum).toFixed(1)}`).join(' ')} L${x(n - 1).toFixed(1)},${baseY} Z` : '';
  const gainBand = n ?
  `M${series.map((d, i) => `${i === 0 ? 'L' : 'L'}${x(i).toFixed(1)},${y(d.marketCum).toFixed(1)}`).join(' ').slice(1)} ${series.slice().reverse().map((d, ri) => `L${x(n - 1 - ri).toFixed(1)},${y(d.costCum).toFixed(1)}`).join(' ')} Z` :
  '';

  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * maxY);
  const xLabelEvery = Math.max(1, Math.ceil(n / 7));

  const onMove = (e) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const mx = (e.clientX - rect.left) * (w / rect.width);
    let i = Math.round((mx - padL) / innerW * (n - 1));
    i = Math.max(0, Math.min(n - 1, i));
    setHover(i);
  };

  const hv = hover != null ? series[hover] : null;
  const tipLeft = hover != null ? Math.min(Math.max(x(hover), 90), w - 90) : 0;
  const tipSide = hover != null && x(hover) > w * 0.62 ? 'right' : 'left';

  return (
    <div ref={wrapRef} style={{ position: 'relative', width: '100%' }}>
      <svg width="100%" height={H} viewBox={`0 0 ${w} ${H}`} style={{ display: 'block', overflow: 'visible' }}
      onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        <defs>
          <linearGradient id="vc-gain" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--gold)" stopOpacity="0.28" />
            <stop offset="100%" stopColor="var(--gold)" stopOpacity="0.04" />
          </linearGradient>
          <linearGradient id="vc-cost" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--copper)" stopOpacity="0.30" />
            <stop offset="100%" stopColor="var(--copper)" stopOpacity="0.06" />
          </linearGradient>
        </defs>

        {/* gridlines + y labels */}
        {ticks.map((t, i) =>
        <g key={i}>
            <line x1={padL} y1={y(t)} x2={w - padR} y2={y(t)} stroke="var(--border)" strokeWidth="1" strokeOpacity={i === 0 ? 0.8 : 0.4} />
            <text x={padL - 10} y={y(t) + 3} textAnchor="end" fontFamily="var(--mono)" fontSize="10" fill="var(--muted)">{fmtAxis(t)}</text>
          </g>
        )}

        {/* x labels */}
        {series.map((d, i) => i % xLabelEvery === 0 || i === n - 1 ?
        <text key={d.ym} x={x(i)} y={H - 10} textAnchor="middle" fontFamily="var(--mono)" fontSize="10" fill="var(--muted)">{d.label}</text> :
        null)}

        {/* areas */}
        <path d={gainBand} fill="url(#vc-gain)" />
        <path d={costArea} fill="url(#vc-cost)" />

        {/* lines */}
        <path d={costLine} fill="none" stroke="var(--copper)" strokeWidth="1.5" strokeOpacity="0.85" />
        <path className="vc-market" d={marketLine} fill="none" stroke="var(--gold)" strokeWidth="2.5" pathLength="1" />

        {/* hover */}
        {hv &&
        <g>
            <line x1={x(hover)} y1={padT} x2={x(hover)} y2={baseY} stroke="var(--gold)" strokeWidth="1" strokeOpacity="0.5" strokeDasharray="3 3" />
            <circle cx={x(hover)} cy={y(hv.costCum)} r="3.5" fill="var(--bg)" stroke="var(--copper)" strokeWidth="1.5" />
            <circle cx={x(hover)} cy={y(hv.marketCum)} r="4" fill="var(--bg)" stroke="var(--gold)" strokeWidth="2" />
          </g>
        }
      </svg>

      {hv &&
      <div className="vc-tip" style={{ left: tipLeft, transform: `translateX(${tipSide === 'right' ? '-100%' : '0'})`, marginLeft: tipSide === 'right' ? -10 : 10 }}>
          <div className="vc-tip-month">{hv.label}</div>
          <div className="vc-tip-row"><span className="sw gold"></span>Market<b>{fmtFull(hv.marketCum)}</b></div>
          <div className="vc-tip-row"><span className="sw copper"></span>Cost<b>{fmtFull(hv.costCum)}</b></div>
          <div className="vc-tip-row gain"><span className="sw band"></span>Gain<b>{hv.gainCum >= 0 ? '+' : '−'}{fmtFull(Math.abs(hv.gainCum))}</b></div>
          {hv.marketAdd > 0 && <div className="vc-tip-add">+{fmtFull(hv.marketAdd)} added · {hv.cardsAdd.toLocaleString()} cards</div>}
        </div>
      }
    </div>);

}

window.Valuation = Valuation;