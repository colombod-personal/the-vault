// Insights view — analytical cuts of your collection. No enrichment required for the core.
const { useState: useStateL, useMemo: useMemoL } = React;

function Lab({ data, openCard }) {
  const [tick, setTick] = useStateL(0);
  const [enriching, setEnriching] = useStateL(false);
  const [progress, setProgress] = useStateL({ done: 0, total: 0 });
  const [pnlMode, setPnlMode] = useStateL('winners'); // winners | losers
  const [typeFocus, setTypeFocus] = useStateL(null);

  // ----- Stats that don't need Scryfall -----
  const stats = useMemoL(() => {
    let biggestGain = null, biggestLoss = null;
    let foilValue = 0, totalValue = 0;
    let totalPaid = 0, totalCards = 0;
    let maxQty = null;
    for (const c of data.cards) {
      const total = c.mk * c.q;
      const paid = c.pd;
      const pnl = total - paid;
      if (!biggestGain || pnl > biggestGain.pnl) biggestGain = { c, pnl };
      if (!biggestLoss || pnl < biggestLoss.pnl) biggestLoss = { c, pnl };
      totalValue += total;
      totalPaid += paid;
      totalCards += c.q;
      if (c.p && c.p.includes('Foil')) foilValue += total;
      if (!maxQty || c.q > maxQty.q) maxQty = { c, q: c.q };
    }
    return {
      biggestGain, biggestLoss,
      foilPct: totalValue > 0 ? (foilValue / totalValue) * 100 : 0,
      foilValue,
      avgPaidPerCard: totalCards > 0 ? totalPaid / totalCards : 0,
      maxQty,
      totalValue,
    };
  }, [data]);

  // P&L lists (winners / losers) per printing
  const pnlList = useMemoL(() => {
    const withPnL = data.cards.map(c => ({
      c,
      total: c.mk * c.q,
      paid: c.pd,
      pnl: c.mk * c.q - c.pd,
      pnlPct: c.pd > 0 ? ((c.mk * c.q - c.pd) / c.pd) * 100 : null,
    })).filter(x => x.paid > 0);
    return {
      winners: withPnL.slice().sort((a, b) => b.pnl - a.pnl).slice(0, 8),
      losers: withPnL.slice().sort((a, b) => a.pnl - b.pnl).slice(0, 8),
    };
  }, [data]);

  // Biggest stockpiles (most duplicates owned)
  const stockpiles = useMemoL(() => {
    const byName = {};
    for (const c of data.cards) {
      const k = c.n.toLowerCase();
      if (!byName[k]) byName[k] = { name: c.n, total: 0, value: 0, entries: [], firstC: c };
      byName[k].total += c.q;
      byName[k].value += c.mk * c.q;
      byName[k].entries.push(c);
    }
    return Object.values(byName)
      .sort((a, b) => b.total - a.total)
      .slice(0, 12);
  }, [data]);

  // Acquisition spend over time (months)
  const spendTimeline = useMemoL(() => {
    const months = {};
    for (const c of data.cards) {
      const m = (c.fd || '').slice(0, 7);
      if (!m) continue;
      months[m] = (months[m] || 0) + c.pd;
    }
    return Object.entries(months)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([month, spend]) => ({ month, spend: +spend.toFixed(2) }));
  }, [data]);

  // ----- Scryfall-enriched data -----
  const enriched = useMemoL(() => {
    const out = [];
    for (const c of data.cards) {
      const s = window.Scryfall.cached(c.n, c.s, c.cn);
      if (s) out.push({ ...c, scry: s });
    }
    return out;
  }, [data, tick]);
  const coverage = enriched.length / data.cards.length;

  async function enrichBatch(n = 600) {
    setEnriching(true);
    const todo = data.cards
      .filter(c => !window.Scryfall.cached(c.n, c.s, c.cn))
      .sort((a, b) => (b.mk * b.q) - (a.mk * a.q))
      .slice(0, n)
      .map(c => ({ name: c.n, set: c.s, collector_number: c.cn }));
    setProgress({ done: 0, total: todo.length });
    await window.Scryfall.collection(todo, p => setProgress(p));
    setTick(t => t + 1);
    setEnriching(false);
  }

  // Color / type aggregations from enrichment
  const byColor = useMemoL(() => {
    const buckets = { W: 0, U: 0, B: 0, R: 0, G: 0, C: 0, M: 0 };
    const buckVal = { W: 0, U: 0, B: 0, R: 0, G: 0, C: 0, M: 0 };
    for (const c of enriched) {
      const ci = c.scry.color_identity || [];
      const key = ci.length === 0 ? 'C' : ci.length === 1 ? ci[0] : 'M';
      buckets[key] += c.q;
      buckVal[key] += c.mk * c.q;
    }
    return { buckets, buckVal };
  }, [enriched]);

  const byType = useMemoL(() => {
    const types = {};
    for (const c of enriched) {
      const tl = c.scry.type_line || '';
      const main = tl.split(' — ')[0].split(' ').pop();
      if (!types[main]) types[main] = { qty: 0, value: 0 };
      types[main].qty += c.q;
      types[main].value += c.mk * c.q;
    }
    return Object.entries(types).map(([t, v]) => ({ type: t, ...v })).sort((a, b) => b.qty - a.qty);
  }, [enriched]);

  const byCmc = useMemoL(() => {
    const buckets = {};
    for (const c of enriched) {
      const cmc = Math.min(Math.floor(c.scry.cmc ?? 0), 8);
      buckets[cmc] = (buckets[cmc] || 0) + c.q;
    }
    return buckets;
  }, [enriched]);

  return (
    <div data-screen-label="05 Insights">
      <div style={{ display: 'flex', alignItems: 'end', justifyContent: 'space-between', marginBottom: 28 }}>
        <div>
          <p className="eyebrow">Insights</p>
          <h1 className="h1" style={{ marginTop: 6 }}>What your numbers reveal.</h1>
        </div>
      </div>

      {/* Hero stats — no enrichment required */}
      <div className="stat-grid">
        <div className="stat good">
          <div className="label">Biggest winner</div>
          <div className="value" style={{ fontSize: 28 }}>
            <span className="currency">$</span>+{stats.biggestGain ? stats.biggestGain.pnl.toFixed(0) : 0}
          </div>
          {stats.biggestGain && (
            <div className="delta">{stats.biggestGain.c.n} [{stats.biggestGain.c.s}]</div>
          )}
        </div>
        <div className="stat bad">
          <div className="label">Biggest loser</div>
          <div className="value" style={{ fontSize: 28, color: 'var(--danger)' }}>
            <span className="currency">$</span>{stats.biggestLoss ? stats.biggestLoss.pnl.toFixed(0) : 0}
          </div>
          {stats.biggestLoss && (
            <div className="delta">{stats.biggestLoss.c.n} [{stats.biggestLoss.c.s}]</div>
          )}
        </div>
        <div className="stat accent">
          <div className="label">Foil share of value</div>
          <div className="value">{stats.foilPct.toFixed(1)}<span style={{ fontSize: 18, color: 'var(--muted)' }}>%</span></div>
          <div className="delta">${stats.foilValue.toFixed(0)} of foil cards</div>
        </div>
        <div className="stat">
          <div className="label">Avg paid per card</div>
          <div className="value"><span className="currency">$</span>{stats.avgPaidPerCard.toFixed(2)}</div>
          <div className="delta">your average pull cost</div>
        </div>
      </div>

      {/* P&L panel */}
      <div className="section">
        <div className="section-head">
          <div>
            <p className="eyebrow">Profit &amp; loss</p>
            <h2 className="h2" style={{ marginTop: 4 }}>Cards by P&amp;L vs purchase price</h2>
          </div>
          <div className="row" style={{ gap: 4 }}>
            <button className={`chip ${pnlMode === 'winners' ? 'active' : ''}`} onClick={() => setPnlMode('winners')}>Winners</button>
            <button className={`chip ${pnlMode === 'losers' ? 'active' : ''}`} onClick={() => setPnlMode('losers')}>Losers</button>
          </div>
        </div>
        <div className="panel panel-flush">
          <table className="tbl">
            <thead>
              <tr>
                <th>Card</th>
                <th>Set</th>
                <th className="num">Qty</th>
                <th className="num">Spent</th>
                <th className="num">Now</th>
                <th className="num">P&amp;L</th>
                <th className="num">%</th>
              </tr>
            </thead>
            <tbody>
              {pnlList[pnlMode].map((row, i) => (
                <tr key={i} onClick={() => openCard(row.c)} style={{ cursor: 'pointer' }}>
                  <td style={{ fontWeight: 600 }}>{row.c.n}</td>
                  <td>
                    <span className="chip" style={{ padding: '2px 8px', fontSize: 10, gap: 4 }}>
                      {window.SetIcon && <SetIcon code={row.c.s} size={12} />}
                      <span>{row.c.s}</span>
                    </span>
                  </td>
                  <td className="num">{row.c.q}</td>
                  <td className="num muted">${row.paid.toFixed(2)}</td>
                  <td className="num" style={{ color: 'var(--gold)' }}>${row.total.toFixed(2)}</td>
                  <td className="num" style={{ color: row.pnl >= 0 ? 'var(--good)' : 'var(--danger)' }}>
                    {row.pnl >= 0 ? '+' : '−'}${Math.abs(row.pnl).toFixed(2)}
                  </td>
                  <td className="num" style={{ color: row.pnl >= 0 ? 'var(--good)' : 'var(--danger)' }}>
                    {row.pnlPct === null ? '—' : `${row.pnl >= 0 ? '+' : ''}${row.pnlPct.toFixed(0)}%`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Stockpiles + Spend timeline */}
      <div className="col-2 section">
        <div className="panel">
          <p className="eyebrow">Biggest stockpiles</p>
          <h2 className="h2" style={{ marginTop: 4, fontSize: 22, marginBottom: 14 }}>Cards you own most copies of</h2>
          {stockpiles.map((s, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'center', padding: '8px 0', borderBottom: '1px solid oklch(0.36 0.014 65 / 0.4)', cursor: 'pointer' }} onClick={() => openCard(s.firstC)}>
              <span style={{ flex: 1, fontWeight: 500 }}>{s.name}</span>
              <span style={{ fontFamily: 'var(--mono)', fontSize: 12, color: 'var(--muted)', marginRight: 12 }}>{s.entries.length} prints</span>
              <span style={{ fontFamily: 'var(--mono)', fontSize: 13, color: 'var(--gold)', minWidth: 50, textAlign: 'right' }}>×{s.total}</span>
            </div>
          ))}
        </div>

        <div className="panel">
          <p className="eyebrow">Cadence</p>
          <h2 className="h2" style={{ marginTop: 4, fontSize: 22, marginBottom: 14 }}>Acquisition spend by month</h2>
          <SpendChart data={spendTimeline} />
          <div className="divider"></div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <div>
              <p className="label-mono">Total spent</p>
              <p style={{ fontFamily: 'var(--display)', fontSize: 24, fontWeight: 600, marginTop: 4, lineHeight: 1 }}>${data.meta.totalPaid.toLocaleString(undefined, { maximumFractionDigits: 0 })}</p>
            </div>
            <div>
              <p className="label-mono">Most duplicated</p>
              <p style={{ fontSize: 13, marginTop: 4, lineHeight: 1.3 }}>
                {stats.maxQty?.c?.n}<br/>
                <span style={{ fontFamily: 'var(--mono)', color: 'var(--gold)', fontSize: 12 }}>×{stats.maxQty?.q}</span>
              </p>
            </div>
          </div>
        </div>
      </div>

      {/* Enrichment-dependent section */}
      <div className="section">
        <div className="section-head">
          <div>
            <p className="eyebrow">By the five colors</p>
            <h2 className="h2" style={{ marginTop: 4 }}>Color, type and curve</h2>
          </div>
          <div className="row">
            <span className="label-mono">{enriched.length.toLocaleString()} / {data.cards.length.toLocaleString()} enriched</span>
            <button className="btn primary" onClick={() => enrichBatch(600)} disabled={enriching}>
              {enriching ? <><span className="spinner"></span> Enriching ({progress.done}/{progress.total})</> : 'Enrich +600 →'}
            </button>
            <button className="btn ghost" onClick={() => enrichBatch(2000)} disabled={enriching}>+2000</button>
          </div>
        </div>

        {enriched.length === 0 ? (
          <div className="panel" style={{ padding: 40, textAlign: 'center' }}>
            <p className="muted" style={{ fontSize: 13 }}>Click "Enrich +600" to fetch color, type and mana data from Scryfall and unlock the breakdowns below. Cache persists across reloads.</p>
          </div>
        ) : (
          <>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', gap: 10, marginBottom: 20 }}>
              {[
                ['W', 'White'], ['U', 'Blue'], ['B', 'Black'], ['R', 'Red'], ['G', 'Green'],
                ['M', 'Multi'], ['C', 'Colorless'],
              ].map(([k, name]) => {
                const total = Object.values(byColor.buckets).reduce((a, b) => a + b, 0) || 1;
                const pct = (byColor.buckets[k] / total) * 100;
                return (
                  <div key={k} className="panel" style={{ padding: 14, textAlign: 'center', position: 'relative', overflow: 'hidden' }}>
                    <div style={{ position: 'absolute', bottom: 0, left: 0, right: 0, height: 3, background: `var(--mana-${k.toLowerCase()})` }}></div>
                    <span className={`pip ${k}`} style={{ width: 28, height: 28, fontSize: 13, marginBottom: 6 }}>{k}</span>
                    <div className="label-mono" style={{ marginTop: 6 }}>{name}</div>
                    <div style={{ fontFamily: 'var(--display)', fontSize: 22, fontWeight: 600, marginTop: 4, lineHeight: 1 }}>{byColor.buckets[k].toLocaleString()}</div>
                    <div style={{ fontFamily: 'var(--mono)', fontSize: 11, marginTop: 4, color: 'var(--gold)' }}>${byColor.buckVal[k].toLocaleString(undefined, { maximumFractionDigits: 0 })}</div>
                  </div>
                );
              })}
            </div>

            <div className="col-2">
              <div className="panel">
                <p className="eyebrow">Card types</p>
                <h2 className="h2" style={{ marginTop: 4, fontSize: 22, marginBottom: 12 }}>What's in your library</h2>
                <p className="muted" style={{ fontSize: 11, marginBottom: 10 }}>Click a type to expand its priciest cards.</p>
                {byType.slice(0, 10).map(t => {
                  const maxT = byType[0].qty || 1;
                  const isOpen = typeFocus === t.type;
                  return (
                    <div key={t.type} style={{ marginBottom: 10 }}>
                      <button onClick={() => setTypeFocus(isOpen ? null : t.type)} style={{ display: 'block', width: '100%', textAlign: 'left', padding: 0 }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13, marginBottom: 4 }}>
                          <span style={{ color: isOpen ? 'var(--gold)' : 'var(--text)' }}>{isOpen ? '▼ ' : '▸ '}{t.type}</span>
                          <span style={{ fontFamily: 'var(--mono)', color: 'var(--muted)' }}>
                            {t.qty.toLocaleString()} · <span style={{ color: 'var(--gold)' }}>${t.value.toFixed(0)}</span>
                          </span>
                        </div>
                        <div className="progress-bar"><div style={{ width: `${(t.qty / maxT) * 100}%`, background: 'linear-gradient(90deg, var(--gold), var(--copper))' }}></div></div>
                      </button>
                      {isOpen && (
                        <div style={{ padding: '10px 0 4px 14px', borderLeft: '1px solid var(--border)', marginTop: 6, marginLeft: 4 }}>
                          {enriched
                            .filter(c => ((c.scry.type_line || '').split(' — ')[0].split(' ').pop() || 'Other') === t.type)
                            .sort((a, b) => b.mk - a.mk)
                            .slice(0, 8)
                            .map((c, i) => (
                              <button key={i} onClick={() => openCard(c)} style={{ display: 'flex', justifyContent: 'space-between', width: '100%', padding: '4px 0', borderBottom: '1px solid oklch(0.36 0.014 65 / 0.4)', fontSize: 12, textAlign: 'left' }}>
                                <span><span className="muted" style={{ fontFamily: 'var(--mono)', fontSize: 10 }}>{c.s}</span> {c.n}</span>
                                <span style={{ fontFamily: 'var(--mono)', color: 'var(--gold)' }}>${c.mk.toFixed(2)}</span>
                              </button>
                            ))}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>

              <div className="panel">
                <p className="eyebrow">Mana curve</p>
                <h2 className="h2" style={{ marginTop: 4, fontSize: 22, marginBottom: 14 }}>Mana value distribution</h2>
                <div style={{ display: 'flex', alignItems: 'end', gap: 6, height: 220 }}>
                  {[0,1,2,3,4,5,6,7,8].map(n => {
                    const total = Object.values(byCmc).reduce((a, b) => a + b, 0) || 1;
                    const v = byCmc[n] || 0;
                    const max = Math.max(...Object.values(byCmc), 1);
                    return (
                      <div key={n} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
                        <div style={{ fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--muted)' }}>{((v / total) * 100).toFixed(0)}%</div>
                        <div style={{ width: '100%', height: `${(v / max) * 160}px`, background: 'linear-gradient(180deg, var(--gold), var(--copper))', borderRadius: '2px 2px 0 0', minHeight: 2 }}></div>
                        <div style={{ fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--text)' }}>{n === 8 ? '8+' : n}</div>
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function SpendChart({ data }) {
  if (!data?.length) return <p className="muted">No spend data.</p>;
  const w = 600, h = 140, pad = 20;
  const maxS = Math.max(...data.map(d => d.spend));
  const step = (w - pad * 2) / Math.max(data.length - 1, 1);
  const pts = data.map((d, i) => [pad + i * step, h - pad - (d.spend / maxS) * (h - pad * 2)]);
  const bars = data.map((d, i) => {
    const bw = Math.max(1, step * 0.7);
    const x = pad + i * step - bw / 2;
    const yTop = h - pad - (d.spend / maxS) * (h - pad * 2);
    return { x, y: yTop, w: bw, h: h - pad - yTop, d };
  });
  const peak = data.reduce((m, d, i) => d.spend > data[m].spend ? i : m, 0);
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" style={{ width: '100%', height: 160 }}>
      {bars.map((b, i) => (
        <rect key={i} x={b.x} y={b.y} width={b.w} height={b.h} fill="oklch(0.66 0.13 50 / 0.6)" rx="1" />
      ))}
      <text x={bars[peak].x + bars[peak].w / 2} y={bars[peak].y - 6} fontSize="9" fill="var(--gold)" textAnchor="middle" fontFamily="var(--mono)">
        ${data[peak].spend.toFixed(0)} {data[peak].month}
      </text>
    </svg>
  );
}

window.Lab = Lab;
