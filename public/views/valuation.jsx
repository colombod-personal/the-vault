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
  const api = data.api;
  const fresh = window.vaultFreshness(m.generatedAt);

  // The server's monthly series (GET /collection/valuation): each purchase month's copies, valued
  // at current prices, with running totals of value, cost and gain, the last 12 months and the
  // copies without a purchase date. The day-by-day value is the server's history.
  const at = [api.base, m.version];
  const valuation = window.useVaultQuery(() => api.valuation(), at).data;
  const history = window.useVaultQuery(() => api.history(), at).data;
  const full = useMemoVal(() => (valuation ? valuation.months : []).map((v) => ({
    ym: v.month, label: monthLabel(v.month),
    marketCum: v.market_cum, costCum: v.cost_cum || 0, gainCum: v.gain_cum || 0, cardsCum: v.copies_cum,
    marketAdd: v.market, costAdd: v.paid || 0, cardsAdd: v.copies,
  })), [valuation]);

  const [range, setRange] = useStateVal('all');
  const series = useMemoVal(() => {
    if (range === 'all') return full;
    const n = parseInt(range, 10);
    return full.slice(Math.max(0, full.length - n));
  }, [full, range]);

  // The headline counts every card, dated or not; gain counts only cards with a known cost (the
  // summary's P&L).
  const gainKnown = m.pnl != null;
  const gain = m.pnl || 0;
  const pnlPct = m.pnlPct || 0;
  const undated = valuation ? valuation.undated.copies : 0;
  // Market value of the copies bought in the last 12 months the collection has purchases in.
  const trailing = valuation && valuation.trailing_12m;
  const yoy = trailing ? trailing.market : 0;

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


  return (
    <div data-screen-label="Valuation">
      <div className="page-head" style={{ display: 'flex', alignItems: 'end', justifyContent: 'space-between', marginBottom: 28 }}>
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
          {window.RefreshButton && (onRefresh || onBulkSync) && (
            <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 6 }}>
              {onRefresh && <RefreshButton refreshing={refreshing} refreshProgress={refreshProgress} onRefresh={onRefresh} />}
              {onBulkSync && (
                <button className="btn ghost xs bulk-link" disabled={refreshing} onClick={onBulkSync}
                  title="Reload the values the server computed from Scryfall's prices (no Scryfall calls from your browser).">
                  ↓ Load server prices
                </button>
              )}
              {refreshing && refreshProgress && (
                <div className="refresh-bar" style={{ width: 168 }}>
                  <div style={{ width: `${refreshProgress.total ? (refreshProgress.done / refreshProgress.total) * 100 : 0}%` }}></div>
                </div>
              )}
              {refreshing && refreshProgress && (
                <span style={{ fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--muted)' }}>
                  {refreshProgress.total ? `${refreshProgress.done.toLocaleString()} / ${refreshProgress.total.toLocaleString()} printings` : 'Starting…'}
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
          <div className="value"><span className="currency">$</span>{Math.round(m.totalMarket).toLocaleString()}</div>
          <div className="delta">at prices as of {fresh.abs}</div>
        </div>
        {m.costsHidden ? (
          <div className="stat">
            <div className="label">Cost basis</div>
            <div className="value muted">private</div>
            <div className="delta">{m.sharedBy || 'The owner'} keeps prices paid private</div>
          </div>
        ) : (<>
        <div className="stat accent">
          <div className="label">Cost basis</div>
          <div className="value"><span className="currency">$</span>{Math.round(m.totalPaid).toLocaleString()}</div>
          <div className="delta">cumulative spend</div>
        </div>
        {gainKnown ? (
          <div className={`stat ${gain >= 0 ? 'good' : 'bad'}`}>
            <div className="label">Unrealised gain</div>
            <div className="value" style={{ color: gain >= 0 ? 'var(--good)' : 'var(--danger)' }}>
              <span className="currency">$</span>{gain >= 0 ? '+' : '−'}{Math.abs(Math.round(gain)).toLocaleString()}
            </div>
            <div className={`delta ${gain >= 0 ? 'up' : 'down'}`}>{gain >= 0 ? '▲' : '▼'} {Math.abs(pnlPct).toFixed(1)}% over cost</div>
            {m.unknownCostCopies > 0 && <div className="delta" style={{ fontSize: 10 }}>on the {m.knownCostCopies.toLocaleString()} cards with a price paid</div>}
          </div>
        ) : (
          <div className="stat">
            <div className="label">Unrealised gain</div>
            <div className="value muted">—</div>
            <div className="delta">no prices paid recorded</div>
          </div>
        )}
        </>)}
        <div className="stat">
          <div className="label">Last 12 months</div>
          <div className="value" style={{ color: 'var(--gold)' }}><span className="currency">$</span>+{Math.round(yoy).toLocaleString()}</div>
          <div className="delta">{trailing ? `value added since ${monthLabel(trailing.since)}` : 'no acquisition dates recorded'}</div>
        </div>
      </div>
      {undated > 0 && (
        <p className="label-mono" style={{ marginBottom: 16 }}>
          {undated.toLocaleString()} {undated === 1 ? 'card has' : 'cards have'} no acquisition date: counted in the market value above, left out of the month-by-month curve and ledger.
        </p>
      )}

      <DailyValue history={history} />

      <div className="panel" style={{ marginBottom: 24 }}>
        <div className="section-head" style={{ marginBottom: 18 }}>
          <div>
            <p className="eyebrow">Growth curve</p>
            <h2 className="h2" style={{ marginTop: 4, fontSize: 22 }}>{m.costsHidden ? 'Cumulative value' : 'Cumulative value vs. cost basis'}</h2>
          </div>
          <div style={{ display: 'flex', gap: 6 }}>
            {[['all', 'All'], ['24', '24M'], ['12', '12M']].map(([k, lbl]) =>
            <button key={k} className={`chip ${range === k ? 'active' : ''}`} onClick={() => setRange(k)}>{lbl}</button>
            )}
          </div>
        </div>

        <div style={{ display: 'flex', gap: 20, marginBottom: 14, flexWrap: 'wrap' }}>
          <Legend swatch="gold" label="Market value (at current prices)" />
          {!m.costsHidden && <Legend swatch="copper" label="Cost basis (what you paid)" />}
          {!m.costsHidden && <Legend swatch="band" label="Unrealised gain" />}
        </div>

        <ValueChart series={series} costsHidden={m.costsHidden} />

        <p className="muted" style={{ fontSize: 11, fontFamily: 'var(--mono)', lineHeight: 1.6, marginTop: 16, maxWidth: 760 }}>
          Each point is your holdings as of that month. The cost line is what you actually paid over
          time; the market line values those same holdings at the latest prices (calculated {fresh.abs}).
          The gold band between them is unrealised gain. For what your collection was actually worth on
          each day, see the price history above.
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
              {!m.costsHidden && <option value="spend">Most spent</option>}
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
                  {!m.costsHidden && <th className="num">Spend</th>}
                  <th className="num">Market value added</th>
                  <th className="num">Cumulative value</th>
                </tr>
              </thead>
              <tbody>
                {ledgerRows.map((r) =>
                <tr key={r.ym}>
                    <td>{r.label}</td>
                    <td className="num muted">{r.cardsAdd.toLocaleString()}</td>
                    {!m.costsHidden && <td className="num muted">${Math.round(r.costAdd).toLocaleString()}</td>}
                    <td className="num" style={{ color: 'var(--gold)' }}>${Math.round(r.marketAdd).toLocaleString()}</td>
                    <td className="num">${Math.round(r.marketCum).toLocaleString()}</td>
                  </tr>
                )}
                {ledgerRows.length === 0 &&
                <tr><td colSpan={m.costsHidden ? 4 : 5} className="muted" style={{ textAlign: 'center', padding: 24 }}>No months match “{ledgerQuery}”.</td></tr>
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

// ---- Real value over time: one point per day, recorded by the daily price sync (and on import) ----

const DAY_RANGES = [['30', '30D'], ['90', '90D'], ['365', '1Y'], ['all', 'All']];
const fmtDay = (iso, opts) => new Date(iso + 'T00:00:00').toLocaleDateString(undefined, opts || { day: 'numeric', month: 'short', year: 'numeric' });
const fmtMoney = (v) => '$' + (Math.round(v * 100) / 100).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function DailyValue({ history }) {
  const days = useMemoVal(() => (history || []).filter((d) => d && d.day).slice().sort((a, b) => a.day.localeCompare(b.day)), [history]);
  const [range, setRange] = useStateVal('90');
  const shown = useMemoVal(() => {
    if (range === 'all' || !days.length) return days;
    const from = new Date(days[days.length - 1].day + 'T00:00:00');
    from.setDate(from.getDate() - parseInt(range, 10));
    const iso = from.toISOString().slice(0, 10);
    return days.filter((d) => d.day >= iso);
  }, [days, range]);

  const last = days[days.length - 1];
  const first = shown[0];
  const change = last && first ? last.market - first.market : 0;
  const changePct = first && first.market ? (change / first.market) * 100 : 0;
  const up = change >= 0;

  return (
    <div className="panel daily-panel" style={{ marginBottom: 24 }}>
      <div className="section-head" style={{ marginBottom: 14, flexWrap: 'wrap', gap: 12 }}>
        <div>
          <p className="eyebrow">Price history</p>
          <h2 className="h2" style={{ marginTop: 4, fontSize: 22 }}>Market value, day by day</h2>
        </div>
        {days.length > 1 && (
          <div style={{ display: 'flex', gap: 6 }} role="group" aria-label="Time range">
            {DAY_RANGES.map(([k, lbl]) => (
              <button key={k} className={`chip ${range === k ? 'active' : ''}`} aria-pressed={range === k} onClick={() => setRange(k)}>{lbl}</button>
            ))}
          </div>
        )}
      </div>

      {!days.length && (
        <p className="daily-empty">
          No daily values yet. The Vault records your collection's market value every day after the price sync,
          starting from your first import.
        </p>
      )}

      {days.length === 1 && (
        <p className="daily-empty">
          Your history starts on {fmtDay(last.day)} at <b>{fmtMoney(last.market)}</b>. A new point is added every day
          after the price sync, so the trend appears from tomorrow.
        </p>
      )}

      {days.length > 1 && (
        <>
          <div className="daily-summary">
            <div>
              <span className="label-mono">Now</span>
              <b>{fmtMoney(last.market)}</b>
            </div>
            <div>
              <span className="label-mono">Change since {fmtDay(first.day, { day: 'numeric', month: 'short' })}</span>
              <b style={{ color: up ? 'var(--good)' : 'var(--danger)' }}>
                {up ? '▲ +' : '▼ −'}{fmtMoney(Math.abs(change))} ({up ? '+' : '−'}{Math.abs(changePct).toFixed(1)}%)
              </b>
            </div>
            {last.cost != null && (
              <div>
                <span className="label-mono">Paid</span>
                <b>{fmtMoney(last.cost)}</b>
              </div>
            )}
          </div>
          <DailyChart days={shown} />
          <p className="daily-note">
            Each point is what your collection was worth that day, at that day's Scryfall prices. Marked days are
            imports, so a jump there can be cards added, removed or swapped, not the market.
          </p>
        </>
      )}
    </div>
  );
}

function DailyChart({ days }) {
  const wrapRef = useRefVal(null);
  const [w, setW] = useStateVal(900);
  const [hover, setHover] = useStateVal(null);
  useLayoutEffectVal(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => { for (const e of entries) setW(Math.max(280, e.contentRect.width)); });
    ro.observe(el);
    setW(Math.max(280, el.clientWidth));
    return () => ro.disconnect();
  }, []);

  const H = 260, padL = 58, padR = 14, padT = 12, padB = 28;
  const innerW = w - padL - padR, innerH = H - padT - padB;
  const n = days.length;
  const t0 = new Date(days[0].day + 'T00:00:00').getTime();
  const t1 = new Date(days[n - 1].day + 'T00:00:00').getTime();
  const x = (d) => padL + (t1 === t0 ? innerW / 2 : ((new Date(d.day + 'T00:00:00').getTime() - t0) / (t1 - t0)) * innerW);
  const values = days.flatMap((d) => [d.market, d.cost == null ? d.market : d.cost]);
  let lo = Math.min(...values), hi = Math.max(...values);
  const pad = Math.max((hi - lo) * 0.1, hi * 0.02, 1);
  lo = Math.max(0, lo - pad); hi = hi + pad;
  const y = (v) => padT + innerH - ((v - lo) / (hi - lo)) * innerH;
  const path = (key) => days.filter((d) => d[key] != null).map((d, i) => `${i ? 'L' : 'M'}${x(d).toFixed(1)},${y(d[key]).toFixed(1)}`).join(' ');
  const area = `${path('market')} L${x(days[n - 1]).toFixed(1)},${padT + innerH} L${x(days[0]).toFixed(1)},${padT + innerH} Z`;
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => lo + f * (hi - lo));
  // The server marks import days; older answers without the mark fall back to a change in card count.
  const isImport = (d, i) => d.imported ?? (i > 0 && d.copies !== days[i - 1].copies);
  const imports = days.filter(isImport);
  const labelEvery = Math.max(1, Math.ceil(n / Math.max(2, Math.floor(innerW / 90))));

  const onMove = (e) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const mx = (e.clientX - rect.left) * (w / rect.width);
    let best = 0;
    for (let i = 1; i < n; i++) if (Math.abs(x(days[i]) - mx) < Math.abs(x(days[best]) - mx)) best = i;
    setHover(best);
  };
  const hv = hover != null ? days[hover] : null;
  const prev = hover ? days[hover - 1] : null;
  const tipLeft = hv ? Math.min(Math.max(x(hv), 80), w - 80) : 0;

  return (
    <div ref={wrapRef} style={{ position: 'relative', width: '100%' }}>
      <svg width="100%" height={H} viewBox={`0 0 ${w} ${H}`} role="img" style={{ display: 'block', overflow: 'visible' }}
        aria-label={`Market value from ${fmtDay(days[0].day)} (${fmtMoney(days[0].market)}) to ${fmtDay(days[n - 1].day)} (${fmtMoney(days[n - 1].market)})`}
        onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        <defs>
          <linearGradient id="dv-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--gold)" stopOpacity="0.28" />
            <stop offset="100%" stopColor="var(--gold)" stopOpacity="0" />
          </linearGradient>
        </defs>
        {ticks.map((v, i) => (
          <g key={i}>
            <line x1={padL} x2={w - padR} y1={y(v)} y2={y(v)} stroke="var(--border)" strokeDasharray={i ? '2 4' : ''} />
            <text x={padL - 8} y={y(v) + 4} textAnchor="end" className="dv-axis">{hi - lo < 20 ? '$' + v.toFixed(2) : fmtAxis(v)}</text>
          </g>
        ))}
        {days.map((d, i) => (i % labelEvery === 0 || i === n - 1) && (i === n - 1 || x(days[n - 1]) - x(d) > 60) && (
          <text key={d.day} x={x(d)} y={H - 8} textAnchor={i === 0 ? 'start' : i === n - 1 ? 'end' : 'middle'} className="dv-axis">
            {fmtDay(d.day, { day: 'numeric', month: 'short' })}
          </text>
        ))}
        <path d={area} fill="url(#dv-fill)" />
        {days.some((d) => d.cost != null) && <path d={path('cost')} fill="none" stroke="var(--copper)" strokeWidth="1.5" strokeDasharray="5 4" />}
        <path d={path('market')} fill="none" stroke="var(--gold)" strokeWidth="2.25" strokeLinejoin="round" />
        {imports.map((d) => <circle key={d.day} cx={x(d)} cy={y(d.market)} r="4" fill="var(--bg)" stroke="var(--text-2)" strokeWidth="1.5" />)}
        {hv && (
          <g>
            <line x1={x(hv)} x2={x(hv)} y1={padT} y2={padT + innerH} stroke="var(--border-2)" />
            <circle cx={x(hv)} cy={y(hv.market)} r="4.5" fill="var(--gold)" />
          </g>
        )}
      </svg>
      {hv && (
        <div className="vc-tip" style={{ left: tipLeft, top: 0, transform: 'translateX(-50%)' }}>
          <div className="vc-tip-month">{fmtDay(hv.day)}</div>
          <div className="vc-tip-row"><span className="sw gold"></span>Market<b>{fmtMoney(hv.market)}</b></div>
          {hv.cost != null && <div className="vc-tip-row"><span className="sw copper"></span>Paid<b>{fmtMoney(hv.cost)}</b></div>}
          {isImport(hv, hover) && (
            <div className="vc-tip-add">
              {!prev ? `Import: ${hv.copies.toLocaleString()} cards`
                : prev.copies === hv.copies ? 'Import (same card count)'
                : `Import: ${hv.copies > prev.copies ? '+' : '−'}${Math.abs(hv.copies - prev.copies).toLocaleString()} cards`}
            </div>
          )}
          {hv.priced < hv.copies && <div className="vc-tip-add">{(hv.copies - hv.priced).toLocaleString()} cards at your file's price</div>}
        </div>
      )}
      <div style={{ display: 'flex', gap: 20, marginTop: 10, flexWrap: 'wrap' }}>
        <Legend swatch="gold" label="Market value that day" />
        {days.some((d) => d.cost != null) && <Legend swatch="copper" label="What you paid" />}
      </div>
    </div>
  );
}

function Legend({ swatch, label }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <span className={`legend-sw ${swatch}`}></span>
      <span style={{ fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--text-2)' }}>{label}</span>
    </div>);

}

function ValueChart({ series, costsHidden }) {
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
        {!costsHidden && <path d={gainBand} fill="url(#vc-gain)" />}
        {!costsHidden && <path d={costArea} fill="url(#vc-cost)" />}

        {/* lines */}
        {!costsHidden && <path d={costLine} fill="none" stroke="var(--copper)" strokeWidth="1.5" strokeOpacity="0.85" />}
        <path className="vc-market" d={marketLine} fill="none" stroke="var(--gold)" strokeWidth="2.5" pathLength="1" />

        {/* hover */}
        {hv &&
        <g>
            <line x1={x(hover)} y1={padT} x2={x(hover)} y2={baseY} stroke="var(--gold)" strokeWidth="1" strokeOpacity="0.5" strokeDasharray="3 3" />
            {!costsHidden && <circle cx={x(hover)} cy={y(hv.costCum)} r="3.5" fill="var(--bg)" stroke="var(--copper)" strokeWidth="1.5" />}
            <circle cx={x(hover)} cy={y(hv.marketCum)} r="4" fill="var(--bg)" stroke="var(--gold)" strokeWidth="2" />
          </g>
        }
      </svg>

      {hv &&
      <div className="vc-tip" style={{ left: tipLeft, transform: `translateX(${tipSide === 'right' ? '-100%' : '0'})`, marginLeft: tipSide === 'right' ? -10 : 10 }}>
          <div className="vc-tip-month">{hv.label}</div>
          <div className="vc-tip-row"><span className="sw gold"></span>Market<b>{fmtFull(hv.marketCum)}</b></div>
          {!costsHidden && <div className="vc-tip-row"><span className="sw copper"></span>Cost<b>{fmtFull(hv.costCum)}</b></div>}
          {!costsHidden && <div className="vc-tip-row gain"><span className="sw band"></span>Gain<b>{hv.gainCum >= 0 ? '+' : '−'}{fmtFull(Math.abs(hv.gainCum))}</b></div>}
          {hv.marketAdd > 0 && <div className="vc-tip-add">+{fmtFull(hv.marketAdd)} added · {hv.cardsAdd.toLocaleString()} cards</div>}
        </div>
      }
    </div>);

}

window.Valuation = Valuation;
window.DailyChart = DailyChart;  // the Lab's value-over-time chart (views/lab.jsx)