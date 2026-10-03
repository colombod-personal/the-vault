// Insights view — analytical cuts of your collection, all computed by the server: winners and
// losers and stockpiles (GET /collection/stats), spend by month (/valuation), colour, type and
// mana value (/breakdowns), a type's priciest cards (/names).
const { useState: useStateL } = React;

const LAB_COLORS = [['W', 'White'], ['U', 'Blue'], ['B', 'Black'], ['R', 'Red'], ['G', 'Green'], ['M', 'Multi'], ['C', 'Colorless']];
const LAB_MANA = ['0', '1', '2', '3', '4', '5', '6', '7', '8+'];

function Lab({ data, openCard }) {
  const m = data.meta;
  const api = data.api;
  const [pnlMode, setPnlMode] = useStateL('winners'); // winners | losers
  const [typeFocus, setTypeFocus] = useStateL(null);
  const at = [api.base, m.version];

  const stats = window.useVaultQuery(() => api.stats(12), at).data;
  const valuation = window.useVaultQuery(() => api.valuation(), at).data;
  const breakdowns = window.useVaultQuery(() => api.breakdowns(), at).data;
  const typeCards = window.useVaultQuery(() => (typeFocus ? api.names({ type: typeFocus }, 8) : null),
    [...at, typeFocus]).data;

  // Open a card name's most valuable printing (per-name lists carry no printing of their own).
  const openName = (name) => api.topPrinting(name).then((c) => c && openCard(c)).catch(() => {});

  // P&L rows: the server's biggest gains and losses, over the copies with a known cost only.
  const pnlRow = (c) => ({ c, qty: c.pq, paid: c.pd, total: c.pd + c.gain, pnl: c.gain, pnlPct: c.pd ? (c.gain / c.pd) * 100 : null });
  const pnlList = {
    winners: (stats?.biggest_gains || []).slice(0, 8).map(pnlRow),
    losers: (stats?.biggest_losses || []).slice(0, 8).map(pnlRow),
  };
  const biggestGain = pnlList.winners[0] || null;
  const biggestLoss = pnlList.losers[0] || null;
  const foilShare = stats ? stats.foil_share_of_value : 0;
  const avgPaidPerCard = m.knownCostCopies ? m.knownCostPaid / m.knownCostCopies : 0;
  const stockpiles = stats?.most_copies || [];
  const maxQty = stockpiles[0] || null;
  const spendTimeline = (valuation?.months || []).map((v) => ({ month: v.month, spend: v.paid || 0 }));

  // Colour, type and mana value: the server's buckets ("unknown" = no card data yet).
  const bucket = (list, key) => (list || []).find((b) => b.key === key) || { copies: 0, printings: 0, market_value: 0 };
  const totalPrintings = breakdowns ? breakdowns.totals.printings : 0;
  const withData = totalPrintings - bucket(breakdowns?.colors, 'unknown').printings;
  const byType = (breakdowns?.types || []).filter((t) => t.key !== 'unknown' && t.copies > 0)
    .sort((a, b) => b.copies - a.copies);
  const manaCopies = LAB_MANA.map((k) => bucket(breakdowns?.mana_values, k).copies);
  const manaTotal = manaCopies.reduce((a, b) => a + b, 0) || 1;
  const manaMax = Math.max(...manaCopies, 1);

  return (
    <div data-screen-label="05 Insights">
      <div style={{ display: 'flex', alignItems: 'end', justifyContent: 'space-between', marginBottom: 28 }}>
        <div>
          <p className="eyebrow">Insights</p>
          <h1 className="h1" style={{ marginTop: 6 }}>What your numbers reveal.</h1>
        </div>
      </div>

      {/* Hero stats — no card data required */}
      {m.costsHidden && (
        <p className="label-mono" style={{ marginBottom: 12 }}>
          Prices paid are private for this shared collection, so profit &amp; loss isn't shown.
        </p>
      )}
      <div className="stat-grid">
        <div className="stat good" style={m.costsHidden ? { display: 'none' } : undefined}>
          <div className="label">Biggest winner</div>
          <div className="value" style={{ fontSize: 28 }}>
            {biggestGain ? <><span className="currency">$</span>{labSigned(biggestGain.pnl)}</> : '—'}
          </div>
          {biggestGain && (
            <div className="delta">{biggestGain.c.n} [{biggestGain.c.s}]</div>
          )}
        </div>
        <div className="stat bad" style={m.costsHidden ? { display: 'none' } : undefined}>
          <div className="label">Biggest loser</div>
          <div className="value" style={{ fontSize: 28, color: biggestLoss && biggestLoss.pnl >= 0 ? 'var(--good)' : 'var(--danger)' }}>
            {biggestLoss ? <><span className="currency">$</span>{labSigned(biggestLoss.pnl)}</> : '—'}
          </div>
          {biggestLoss && (
            <div className="delta">{biggestLoss.c.n} [{biggestLoss.c.s}]</div>
          )}
        </div>
        <div className="stat accent">
          <div className="label">Foil share of value</div>
          <div className="value">{(foilShare * 100).toFixed(1)}<span style={{ fontSize: 18, color: 'var(--muted)' }}>%</span></div>
          <div className="delta">${(foilShare * m.totalMarket).toFixed(0)} of foil cards</div>
        </div>
        <div className="stat" style={m.costsHidden ? { display: 'none' } : undefined}>
          <div className="label">Avg paid per card</div>
          <div className="value">{avgPaidPerCard ? <><span className="currency">$</span>{avgPaidPerCard.toFixed(2)}</> : '—'}</div>
          <div className="delta">your average pull cost{avgPaidPerCard ? ' (cards with a price paid)' : ''}</div>
        </div>
      </div>

      {/* P&L panel */}
      <div className="section" style={m.costsHidden ? { display: 'none' } : undefined}>
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
                      {window.SetIcon && <SetIcon code={row.c.s} size={12} fallback={false} />}
                      <span>{row.c.s}</span>
                    </span>
                  </td>
                  <td className="num">{row.qty}</td>
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
            <div key={i} style={{ display: 'flex', alignItems: 'center', padding: '8px 0', borderBottom: '1px solid oklch(0.36 0.014 65 / 0.4)', cursor: 'pointer' }} {...window.vaultPressable(() => openName(s.name), s.name)}>
              <span style={{ flex: 1, fontWeight: 500 }}>{s.name}</span>
              <span style={{ fontFamily: 'var(--mono)', fontSize: 12, color: 'var(--muted)', marginRight: 12 }}>{s.printings} prints</span>
              <span style={{ fontFamily: 'var(--mono)', fontSize: 13, color: 'var(--gold)', minWidth: 50, textAlign: 'right' }}>×{s.copies}</span>
            </div>
          ))}
        </div>

        <div className="panel">
          {!m.costsHidden && (<>
            <p className="eyebrow">Cadence</p>
            <h2 className="h2" style={{ marginTop: 4, fontSize: 22, marginBottom: 14 }}>Acquisition spend by month</h2>
            <SpendChart data={spendTimeline} />
            <div className="divider"></div>
          </>)}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            {!m.costsHidden && (
              <div>
                <p className="label-mono">Total spent</p>
                <p style={{ fontFamily: 'var(--display)', fontSize: 24, fontWeight: 600, marginTop: 4, lineHeight: 1 }}>
                  {m.totalPaid > 0 ? `$${m.totalPaid.toLocaleString(undefined, { maximumFractionDigits: 0 })}` : '—'}
                </p>
              </div>
            )}
            <div>
              <p className="label-mono">Most duplicated</p>
              <p style={{ fontSize: 13, marginTop: 4, lineHeight: 1.3 }}>
                {maxQty?.name}<br/>
                <span style={{ fontFamily: 'var(--mono)', color: 'var(--gold)', fontSize: 12 }}>×{maxQty?.copies}</span>
              </p>
            </div>
          </div>
        </div>
      </div>

      {/* Card-data section */}
      <div className="section">
        <div className="section-head">
          <div>
            <p className="eyebrow">By the five colors</p>
            <h2 className="h2" style={{ marginTop: 4 }}>Color, type and curve</h2>
          </div>
          <div className="row">
            <span className="label-mono">{withData.toLocaleString()} / {totalPrintings.toLocaleString()} with card data</span>
          </div>
        </div>

        {withData === 0 ? (
          <div className="panel" style={{ padding: 40, textAlign: 'center' }}>
            <p className="muted" style={{ fontSize: 13 }}>Card details (color, type, mana value) arrive with the daily sync; these breakdowns fill in once they do.</p>
          </div>
        ) : (
          <>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', gap: 10, marginBottom: 20 }}>
              {LAB_COLORS.map(([k, name]) => {
                const b = bucket(breakdowns?.colors, k);
                return (
                  <div key={k} className="panel" style={{ padding: 14, textAlign: 'center', position: 'relative', overflow: 'hidden' }}>
                    <div style={{ position: 'absolute', bottom: 0, left: 0, right: 0, height: 3, background: `var(--mana-${k.toLowerCase()})` }}></div>
                    <span className={`pip ${k}`} style={{ width: 28, height: 28, fontSize: 13, marginBottom: 6 }}>{k}</span>
                    <div className="label-mono" style={{ marginTop: 6 }}>{name}</div>
                    <div style={{ fontFamily: 'var(--display)', fontSize: 22, fontWeight: 600, marginTop: 4, lineHeight: 1 }}>{b.copies.toLocaleString()}</div>
                    <div style={{ fontFamily: 'var(--mono)', fontSize: 11, marginTop: 4, color: 'var(--gold)' }}>${b.market_value.toLocaleString(undefined, { maximumFractionDigits: 0 })}</div>
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
                  const maxT = byType[0].copies || 1;
                  const isOpen = typeFocus === t.key;
                  return (
                    <div key={t.key} style={{ marginBottom: 10 }}>
                      <button aria-expanded={isOpen} onClick={() => setTypeFocus(isOpen ? null : t.key)} style={{ display: 'block', width: '100%', textAlign: 'left', padding: 0 }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13, marginBottom: 4 }}>
                          <span style={{ color: isOpen ? 'var(--gold)' : 'var(--text)' }}>{isOpen ? '▼ ' : '▸ '}{t.label}</span>
                          <span style={{ fontFamily: 'var(--mono)', color: 'var(--muted)' }}>
                            {t.copies.toLocaleString()} · <span style={{ color: 'var(--gold)' }}>${t.market_value.toFixed(0)}</span>
                          </span>
                        </div>
                        <div className="progress-bar"><div style={{ width: `${(t.copies / maxT) * 100}%`, background: 'linear-gradient(90deg, var(--gold), var(--copper))' }}></div></div>
                      </button>
                      {isOpen && (
                        <div style={{ padding: '10px 0 4px 14px', borderLeft: '1px solid var(--border)', marginTop: 6, marginLeft: 4 }}>
                          {/* the server's most valuable names of this type */}
                          {(typeCards ? typeCards.items : []).map((c) => (
                              <button key={c.name} onClick={() => openName(c.name)} style={{ display: 'flex', justifyContent: 'space-between', width: '100%', padding: '4px 0', borderBottom: '1px solid oklch(0.36 0.014 65 / 0.4)', fontSize: 12, textAlign: 'left' }}>
                                <span><span className="muted" style={{ fontFamily: 'var(--mono)', fontSize: 10 }}>{c.sets.length === 1 ? c.sets[0] : `${c.sets.length} sets`}</span> {c.name}</span>
                                <span style={{ fontFamily: 'var(--mono)', color: 'var(--gold)' }}>${c.unit_price.toFixed(2)}</span>
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
                  {LAB_MANA.map((n, i) => {
                    const v = manaCopies[i];
                    return (
                      <div key={n} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
                        <div style={{ fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--muted)' }}>{((v / manaTotal) * 100).toFixed(0)}%</div>
                        <div style={{ width: '100%', height: `${(v / manaMax) * 160}px`, background: 'linear-gradient(180deg, var(--gold), var(--copper))', borderRadius: '2px 2px 0 0', minHeight: 2 }}></div>
                        <div style={{ fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--text)' }}>{n}</div>
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

// A signed whole-dollar amount: +12 / −3.
const labSigned = (v) => `${v >= 0 ? '+' : '−'}${Math.abs(v).toFixed(0)}`;

function SpendChart({ data }) {
  if (!data?.length) return <p className="muted">No spend data.</p>;
  const w = 600, h = 140, pad = 20;
  const maxS = Math.max(1, ...data.map(d => d.spend));  // no spend recorded: 0, not NaN
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
