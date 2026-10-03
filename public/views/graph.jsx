// Graph view — network/cluster visualisations via Cytoscape.js
const { useState: useStateG, useEffect: useEffectG, useLayoutEffect: useLayoutEffectG, useRef: useRefG, useMemo: useMemoG } = React;

const COLOR_FILL = {
  W: '#e8e1c4', U: '#3a78c9', B: '#3b2a3f', R: '#d04d35', G: '#3f8a5b',
  M: '#c79b3f', C: '#7a7770'
};
const COLOR_NAME = { W: 'White', U: 'Blue', B: 'Black', R: 'Red', G: 'Green', M: 'Multicolor', C: 'Colorless' };
const TYPE_SHAPE = {
  Creature: 'ellipse',
  Land: 'round-rectangle',
  Artifact: 'hexagon',
  Enchantment: 'diamond',
  Instant: 'triangle',
  Sorcery: 'vee',
  Planeswalker: 'pentagon',
  Battle: 'star'
};

// The card's art, cropped (Scryfall art_crop). Older cached cards only kept the normal image,
// whose URL differs only in the size segment.
function artUrl(scry) {
  if (!scry) return null;
  return scry.img_art || (scry.img_normal ? scry.img_normal.replace('/normal/', '/art_crop/') : null);
}

const SAME = { set: 'same set', type: 'same type', color: 'same colors', rarity: 'same rarity', cmc: 'similar mana value', price: 'similar price' };

function GraphView({ data, openCard }) {
  const containerRef = useRefG(null);
  const cyRef = useRefG(null);
  const [mode, setMode] = useStateG('color'); // color | set | type | scatter | affinity | hierarchy | deck
  const [topN, setTopN] = useStateG(200);
  const [minValue, setMinValue] = useStateG(1);
  const [colorFilter, setColorFilter] = useStateG(new Set()); // empty = all
  const [hover, setHover] = useStateG(null);
  const [showArt, setShowArt] = useStateG(true); // card art inside each circle
  const tipRef = useRefG(null);
  // Keep the whole hover card inside the graph, however tall it is (it grows with the links it lists).
  // Measured again when the card image arrives, since that sets most of its height.
  function placeTip() {
    const tip = tipRef.current, box = containerRef.current;
    if (!tip || !box || !hover) return;
    tip.style.top = Math.max(8, Math.min(hover.y + 16, box.offsetHeight - tip.offsetHeight - 8)) + 'px';
  }
  useLayoutEffectG(placeTip, [hover]);
  const [deck, setDeck] = useStateG(null); // {title, rows}
  const [deckUrl, setDeckUrl] = useStateG('https://archidekt.com/decks/5292775/the_dragon_in_the_night');

  // Aggregate by unique card name (using byName index) and join with the card data the
  // collection came with (kept in Postgres by the daily sync)
  const nodes = useMemoG(() => {
    const out = [];
    for (const [k, agg] of Object.entries(data.byName)) {
      const e = agg.entries[0];
      const scry = window.Scryfall.cached(agg.name, e.s, e.cn) || window.Scryfall.cached(agg.name);
      if (!scry) continue;
      const ci = scry.color_identity || [];
      const colorKey = ci.length === 0 ? 'C' : ci.length === 1 ? ci[0] : 'M';
      const mainType = (scry.type_line || '').split(' — ')[0].split(' ').pop() || 'Other';
      out.push({
        id: 'c_' + k.replace(/[^a-z0-9]/gi, '_'),
        name: agg.name,
        qty: agg.total,
        value: agg.value,
        unit: agg.entries.reduce((s, e) => s + e.q * e.mk, 0) / Math.max(1, agg.total),
        color: colorKey,
        ci,
        cmc: scry.cmc ?? 0,
        type: mainType,
        rarity: scry.rarity,
        set: e.s,
        scry,
        entries: agg.entries,
        firstCardObj: { n: agg.name, s: e.s, cn: e.cn, sn: e.sn, p: e.p, c: e.c, l: 'English', q: agg.total, mk: e.mk, lo: 0, mi: 0, pd: 0, fd: '', ld: '' }
      });
    }
    return out;
  }, [data]);

  const withData = nodes.length; // unique names whose card data the server has

  // Filtered nodes
  const filteredNodes = useMemoG(() => {
    let out = nodes;
    if (colorFilter.size > 0) {
      out = out.filter((n) => {
        if (n.color === 'C') return colorFilter.has('C');
        if (n.color === 'M') return n.ci.some((c) => colorFilter.has(c)) || colorFilter.has('M');
        return colorFilter.has(n.color);
      });
    }
    out = out.filter((n) => n.value >= minValue);
    out = out.slice().sort((a, b) => b.value - a.value).slice(0, topN);
    return out;
  }, [nodes, topN, minValue, colorFilter]);

  // How many cards match the color + price filter BEFORE the top-N depth cap
  const matchCount = useMemoG(() => {
    let out = nodes;
    if (colorFilter.size > 0) {
      out = out.filter((n) => {
        if (n.color === 'C') return colorFilter.has('C');
        if (n.color === 'M') return n.ci.some((c) => colorFilter.has(c)) || colorFilter.has('M');
        return colorFilter.has(n.color);
      });
    }
    return out.filter((n) => n.value >= minValue).length;
  }, [nodes, minValue, colorFilter]);

  // Deck overlay: which nodes are in the loaded deck?
  const deckSet = useMemoG(() => {
    if (!deck) return null;
    const s = new Set();
    for (const r of deck.rows) s.add(r.name.toLowerCase().trim());
    return s;
  }, [deck]);

  async function loadDeck() {
    try {
      const d = await window.DeckSrc.fetchUrl(deckUrl.trim());
      setDeck({ title: d.title, rows: d.cards });
      setMode('deck');
    } catch (e) {
      alert('Could not load deck: ' + e.message);
    }
  }

  // Toggle color
  function toggleColor(c) {
    setColorFilter((prev) => {
      const ns = new Set(prev);
      if (ns.has(c)) ns.delete(c);else ns.add(c);
      return ns;
    });
  }

  // ---- Build & render cytoscape ----
  useEffectG(() => {
    if (!containerRef.current) return;
    if (!window.cytoscape) return;
    if (mode === 'hierarchy') {
      // Hierarchy uses its own matrix renderer, not cytoscape
      if (cyRef.current) {cyRef.current.destroy();cyRef.current = null;}
      return;
    }
    if (mode === 'set') {
      // Set mode uses its own bubble renderer
      if (cyRef.current) {cyRef.current.destroy();cyRef.current = null;}
      return;
    }
    if (mode === 'type') {
      // Type mode uses its own roster renderer
      if (cyRef.current) {cyRef.current.destroy();cyRef.current = null;}
      return;
    }
    if (filteredNodes.length === 0) {
      if (cyRef.current) {cyRef.current.destroy();cyRef.current = null;}
      return;
    }

    // Build elements
    const elements = [];
    const parents = new Set();

    function nodeStyle(n) {
      const radius = Math.max(10, Math.min(80, Math.sqrt(n.value) * 4 + 8));
      const inDeck = deckSet ? deckSet.has(n.name.toLowerCase().trim()) : null;
      const shape = mode === 'hierarchy' ? TYPE_SHAPE[n.type] || 'ellipse' : 'ellipse';
      return {
        data: {
          id: n.id,
          label: n.name,
          value: n.value,
          color: COLOR_FILL[n.color],
          radius,
          parent: n._parent,
          rawColor: n.color,
          shape,
          inDeck: inDeck === true ? 'yes' : inDeck === false ? 'no' : '',
          card: n,
          ...(showArt && artUrl(n.scry) ? { art: artUrl(n.scry) } : {})
        }
      };
    }

    if (mode === 'color') {
      // Orbital "WUBRG galaxy": 5 color anchors in a pentagon, Multi in center, Colorless below.
      // Each card orbits its color's anchor; valuable cards land closer to center.
      const W = containerRef.current?.clientWidth || 1200;
      const H = containerRef.current?.clientHeight || 600;
      const cx = W / 2,cy = H / 2 - 20;
      const R = Math.min(W, H) * 0.32; // pentagon radius
      // Pentagon order: W top, U top-right, B bottom-right, R bottom-left, G top-left (clockwise from top)
      const angles = { W: -90, U: -90 + 72, B: -90 + 144, R: -90 + 216, G: -90 + 288 };
      const anchors = {};
      for (const k of ['W', 'U', 'B', 'R', 'G']) {
        const a = angles[k] * Math.PI / 180;
        anchors[k] = [cx + Math.cos(a) * R, cy + Math.sin(a) * R];
      }
      anchors.M = [cx, cy];
      anchors.C = [cx, cy + R + 100];

      const groups = { W: [], U: [], B: [], R: [], G: [], M: [], C: [] };
      for (const n of filteredNodes) groups[n.color].push(n);

      for (const k of Object.keys(groups)) {
        const list = groups[k].slice().sort((a, b) => b.value - a.value);
        if (!list.length) continue;
        const [ax, ay] = anchors[k];
        // Place anchor label as a parent-style node at the anchor
        elements.push({
          data: { id: 'p_' + k, label: COLOR_NAME[k], isAnchor: true, color: COLOR_FILL[k] },
          position: { x: ax, y: ay },
          grabbable: false
        });
        // Spiral / concentric rings around anchor
        list.forEach((n, i) => {
          // Most valuable first → smaller radius
          const ringSize = 8;
          const ring = Math.floor(i / ringSize) + 1;
          const slot = i % ringSize;
          const ringRadius = 40 + ring * 30;
          const angOffset = ring * 0.4; // stagger rings
          const ang = slot / ringSize * 2 * Math.PI + angOffset;
          // Deterministic jitter per name
          let hash = 0;
          for (let j = 0; j < n.name.length; j++) hash = hash * 31 + n.name.charCodeAt(j) >>> 0;
          const r = ringRadius + (hash % 100 / 100 - 0.5) * 12;
          const px = ax + Math.cos(ang) * r;
          const py = ay + Math.sin(ang) * r;
          elements.push({ ...nodeStyle(n), position: { x: px, y: py } });
        });
      }
    } else if (mode === 'type') {
      const groups = {};
      for (const n of filteredNodes) (groups[n.type] = groups[n.type] || []).push(n);
      for (const t of Object.keys(groups)) {
        if (groups[t].length === 0) continue;
        const pid = 'p_' + t.replace(/[^a-z0-9]/gi, '_');
        parents.add(pid);
        elements.push({ data: { id: pid, label: t, isParent: true } });
        for (const n of groups[t]) elements.push(nodeStyle({ ...n, _parent: pid }));
      }
    } else if (mode === 'set') {
      const groups = {};
      for (const n of filteredNodes) (groups[n.set] = groups[n.set] || []).push(n);
      // Sort sets by total value, take top 15 to avoid overcrowding
      const sortedSets = Object.entries(groups).sort((a, b) => {
        const va = a[1].reduce((s, n) => s + n.value, 0);
        const vb = b[1].reduce((s, n) => s + n.value, 0);
        return vb - va;
      }).slice(0, 15);
      for (const [s, list] of sortedSets) {
        const pid = 'p_' + s;
        parents.add(pid);
        elements.push({ data: { id: pid, label: s, isParent: true } });
        for (const n of list) elements.push(nodeStyle({ ...n, _parent: pid }));
      }
    } else if (mode === 'scatter') {
      // Mana value × log(price). Add deterministic horizontal jitter so cards spread out.
      const W = (containerRef.current?.clientWidth || 1200) - 80;
      const H = (containerRef.current?.clientHeight || 600) - 80;
      const maxLog = Math.log10(Math.max(...filteredNodes.map((n) => n.unit), 1) + 1);
      const colW = W / 10;
      for (const n of filteredNodes) {
        // Deterministic jitter per node name
        let hash = 0;
        for (let i = 0; i < n.name.length; i++) hash = hash * 31 + n.name.charCodeAt(i) >>> 0;
        const jitterX = (hash % 1000 / 1000 - 0.5) * colW * 0.8;
        const jitterY = ((hash >>> 10) % 1000 / 1000 - 0.5) * 18;
        const x = 40 + Math.min(n.cmc, 9) * colW + colW / 2 + jitterX;
        const y = H - Math.log10(n.unit + 0.1) / maxLog * (H - 60) - 20 + jitterY;
        elements.push({ ...nodeStyle(n), position: { x, y } });
      }
    } else if (mode === 'affinity') {
      // Build weighted similarity edges between filtered nodes.
      const nlist = filteredNodes;
      const setIdx = {},typeIdx = {},colorIdx = {};
      nlist.forEach((n, i) => {
        (setIdx[n.set] = setIdx[n.set] || []).push(i);
        (typeIdx[n.type] = typeIdx[n.type] || []).push(i);
        (colorIdx[n.color] = colorIdx[n.color] || []).push(i);
      });
      // Score and the shared traits behind it (shown when hovering a card).
      function sim(a, b) {
        let s = 0;
        const why = [];
        if (a.set === b.set) {s += 3.0;why.push('set');}
        if (a.type === b.type) {s += 2.0;why.push('type');}
        if (a.color === b.color) {s += 1.5;why.push('color');}
        if (a.rarity && a.rarity === b.rarity) {s += 0.5;why.push('rarity');}
        if (Math.abs((a.cmc || 0) - (b.cmc || 0)) <= 1) {s += 1.0;why.push('cmc');}
        const lo = Math.min(a.unit, b.unit),hi = Math.max(a.unit, b.unit);
        if (hi > 0 && lo / hi >= 0.6) {s += 1.0;why.push('price');}
        return [s, why];
      }
      nlist.forEach((n) => elements.push(nodeStyle(n)));
      const seen = new Set();
      for (let i = 0; i < nlist.length; i++) {
        const a = nlist[i];
        const cand = new Set([
        ...(setIdx[a.set] || []),
        ...(typeIdx[a.type] || []),
        ...(colorIdx[a.color] || [])]
        );
        const scored = [];
        cand.forEach((j) => {
          if (j === i) return;
          const [sc, why] = sim(a, nlist[j]);
          if (sc >= 3.5) scored.push([j, sc, why]);
        });
        scored.sort((x, y) => y[1] - x[1]);
        scored.slice(0, 4).forEach(([j, sc, why]) => {
          const lo = Math.min(i, j),hi = Math.max(i, j);
          const key = lo + '-' + hi;
          if (seen.has(key)) return;
          seen.add(key);
          elements.push({
            data: {
              id: 'e_' + key,
              source: nlist[lo].id,
              target: nlist[hi].id,
              weight: sc,
              why: why.map((w) => SAME[w]).join(' · ')
            }
          });
        });
      }
    } else if (mode === 'hierarchy') {
      // One-level compound nesting by color; type encoded via node shape (legible without nested grey rects).
      const byColor = {};
      for (const n of filteredNodes) (byColor[n.color] = byColor[n.color] || []).push(n);
      for (const c of Object.keys(byColor)) {
        const cp = 'p_color_' + c;
        elements.push({ data: { id: cp, label: COLOR_NAME[c], isParent: true } });
        for (const n of byColor[c]) elements.push(nodeStyle({ ...n, _parent: cp }));
      }
    } else if (mode === 'deck' && deck) {
      // Show only deck cards. Use byName for owned check.
      const deckRows = deck.rows.map((r) => {
        const key = r.name.toLowerCase().trim();
        const node = nodes.find((n) => n.name.toLowerCase().trim() === key);
        const ownAgg = data.byName[key];
        const owned = ownAgg?.total || 0;
        return { ...r, node, owned, missing: Math.max(0, r.qty - owned) };
      });
      // Group by color of node (if it has card data) else by 'unknown'
      const groups = { W: [], U: [], B: [], R: [], G: [], M: [], C: [], '?': [] };
      for (const r of deckRows) {
        const c = r.node?.color || '?';
        groups[c].push(r);
      }
      for (const k of Object.keys(groups)) {
        if (groups[k].length === 0) continue;
        const pid = 'p_' + k;
        parents.add(pid);
        elements.push({ data: { id: pid, label: COLOR_NAME[k] || 'Unknown', isParent: true } });
        for (const r of groups[k]) {
          const id = 'dk_' + r.name.replace(/[^a-z0-9]/gi, '_');
          const radius = Math.max(8, Math.min(60, Math.sqrt(r.node?.unit || 1) * 5 + 8));
          elements.push({
            data: {
              id, label: r.name, parent: pid,
              radius,
              color: r.node ? COLOR_FILL[r.node.color] : '#444',
              inDeck: r.missing > 0 ? 'missing' : 'owned',
              card: r.node || { firstCardObj: { n: r.name, s: r.set || '?', cn: r.collector_number || '', sn: '', p: 'Normal', c: 'Mint', q: r.owned, mk: 0, l: 'English' } },
              deckQty: r.qty,
              owned: r.owned,
              ...(showArt && artUrl(r.node?.scry) ? { art: artUrl(r.node.scry) } : {})
            }
          });
        }
      }
    }

    // Destroy + create
    if (cyRef.current) cyRef.current.destroy();

    const cy = window.cytoscape({
      container: containerRef.current,
      elements,
      style: [
      {
        selector: 'node[color]',
        style: {
          'background-color': 'data(color)',
          'width': 'data(radius)',
          'height': 'data(radius)',
          'shape': 'data(shape)',
          'border-width': 1.5,
          'border-color': '#3a352b',
          'label': '',
          'transition-property': 'border-color, border-width, opacity',
          'transition-duration': '160ms'
        }
      },
      {
        // The card's art, cut by the circle; its color identity becomes the ring.
        selector: 'node[art]',
        style: {
          'background-image': 'data(art)',
          'background-fit': 'cover',
          'background-clip': 'node',
          'background-image-crossorigin': 'anonymous',
          'border-color': 'data(color)',
          'border-width': 2.5
        }
      },
      {
        selector: 'node[inDeck = "yes"]',
        style: { 'border-color': '#f4d35e', 'border-width': 3 }
      },
      {
        selector: 'node[inDeck = "missing"]',
        style: { 'border-color': '#d04d35', 'border-width': 3, 'border-style': 'dashed' }
      },
      {
        selector: 'node[inDeck = "owned"]',
        style: { 'border-color': '#3f8a5b', 'border-width': 2 }
      },
      {
        selector: 'node[?isAnchor]',
        style: {
          'background-color': 'data(color)',
          'background-opacity': 0.12,
          'border-color': 'data(color)',
          'border-width': 1,
          'border-style': 'dashed',
          'width': 60, 'height': 60,
          'shape': 'ellipse',
          'label': 'data(label)',
          'color': '#c79b3f',
          'font-family': 'JetBrains Mono, monospace',
          'font-size': 13,
          'font-weight': 700,
          'text-valign': 'center',
          'text-halign': 'center',
          'text-transform': 'uppercase'
        }
      },
      {
        selector: 'node[?isParent]',
        style: {
          'padding': 18,
          'background-color': 'rgba(54, 49, 42, 0.45)',
          'border-color': 'rgba(120, 100, 70, 0.6)',
          'border-width': 1,
          'label': 'data(label)',
          'color': '#c79b3f',
          'font-family': 'JetBrains Mono, monospace',
          'font-size': 11,
          'text-valign': 'top',
          'text-halign': 'center',
          'text-margin-y': -6,
          'text-transform': 'uppercase',
          'text-letter-spacing': 1.5,
          'shape': 'round-rectangle'
        }
      },
      {
        selector: 'node:selected',
        style: { 'border-color': '#f4d35e', 'border-width': 4 }
      },
      {
        selector: 'edge',
        style: {
          'line-color': '#c79b3f',
          'opacity': 0.18,
          'width': 'mapData(weight, 3, 9, 0.3, 2)',
          'curve-style': 'haystack',
          'haystack-radius': 0.4
        }
      },
      { selector: '.faded', style: { 'opacity': 0.12 } },
      { selector: 'edge.linked', style: { 'opacity': 0.9, 'line-color': '#f4d35e' } }],

      layout: mode === 'scatter' || mode === 'color' ?
      { name: 'preset', padding: 40, fit: true } :
      mode === 'affinity' ?
      {
        name: 'cose', padding: 30, animate: false, fit: true,
        nodeRepulsion: 8000,
        idealEdgeLength: (edge) => Math.max(20, 200 - (edge.data('weight') || 3) * 18),
        edgeElasticity: (edge) => 50 + (edge.data('weight') || 3) * 30,
        gravity: 0.25, numIter: 1500, randomize: true
      } :
      mode === 'hierarchy' ?
      { name: 'cose', padding: 24, animate: false, fit: true, nodeRepulsion: 8000, idealEdgeLength: 50, gravity: 0.4, nestingFactor: 1.2, numIter: 1200 } :
      { name: 'cose', padding: 30, animate: false, fit: true, nodeRepulsion: 12000, idealEdgeLength: 80, nestingFactor: 0.6, gravity: 0.3, numIter: 800 },
      wheelSensitivity: 0.2,
      minZoom: 0.2,
      maxZoom: 3
    });

    cy.on('tap', 'node', (evt) => {
      const d = evt.target.data();
      if (d.isParent || d.isAnchor) return;
      if (d.card?.firstCardObj) openCard(d.card.firstCardObj);
    });
    cy.on('mouseover', 'node', (evt) => {
      const d = evt.target.data();
      if (d.isParent || d.isAnchor) return;
      const pos = evt.target.renderedPosition();
      let links = null;
      if (mode === 'affinity') {
        const edges = evt.target.connectedEdges();
        cy.elements().not(evt.target.closedNeighborhood()).addClass('faded');
        edges.addClass('linked');
        links = edges.map((e) => ({
          name: (e.source().id() === evt.target.id() ? e.target() : e.source()).data('label'),
          why: e.data('why'),
          weight: e.data('weight')
        })).sort((x, y) => y.weight - x.weight);
      }
      setHover({ d, x: pos.x, y: pos.y, links });
    });
    cy.on('mouseout', 'node', () => {
      cy.elements().removeClass('faded linked');
      setHover(null);
    });
    cy.on('viewport', () => {
      cy.elements().removeClass('faded linked');
      setHover(null);
    });

    // Always fit to viewport after layout completes
    cy.one('layoutstop', () => {
      cy.fit(undefined, 40);
    });

    cyRef.current = cy;
    return () => {cy.destroy();cyRef.current = null;};
  }, [filteredNodes, mode, deck, showArt]);

  // Reset hover on mode change
  useEffectG(() => setHover(null), [mode]);

  return (
    <div data-screen-label="06 Graph">
      <div style={{ marginBottom: 20, display: 'flex', justifyContent: 'space-between', alignItems: 'end' }}>
        <div>
          <p className="eyebrow">The atlas</p>
          <h1 className="h1" style={{ marginTop: 6 }}>Your collection as a network.</h1>
        </div>
        <div style={{ textAlign: 'right' }}>
          <p className="label-mono">Cards with data</p>
          <p style={{ fontFamily: 'var(--mono)', fontSize: 13, marginTop: 4 }}>
            <span style={{ color: 'var(--gold)' }}>{withData.toLocaleString()}</span>
            <span className="muted"> / {Object.keys(data.byName).length.toLocaleString()} unique names</span>
          </p>
        </div>
      </div>

      {/* Controls */}
      <div className="panel" style={{ padding: 14, marginBottom: 12 }}>
        <div style={{ display: 'flex', gap: 16, alignItems: 'center', flexWrap: 'wrap' }}>
          <div className="row" style={{ gap: 4 }}>
            <span className="label-mono" style={{ marginRight: 6 }}>Mode</span>
            <button className={`chip ${mode === 'color' ? 'active' : ''}`} onClick={() => setMode('color')}>Color galaxy</button>
            <button className={`chip ${mode === 'type' ? 'active' : ''}`} onClick={() => setMode('type')}>Type roster</button>
            <button className={`chip ${mode === 'set' ? 'active' : ''}`} onClick={() => setMode('set')}>Set clusters</button>
            <button className={`chip ${mode === 'hierarchy' ? 'active' : ''}`} onClick={() => setMode('hierarchy')}>Hierarchy</button>
            <button className={`chip ${mode === 'affinity' ? 'active' : ''}`} onClick={() => setMode('affinity')}>Affinity web</button>
            <button className={`chip ${mode === 'scatter' ? 'active' : ''}`} onClick={() => setMode('scatter')}>Mana / price</button>
            {deck && <button className={`chip ${mode === 'deck' ? 'active' : ''}`} onClick={() => setMode('deck')}>Deck map</button>}
          </div>
          <button className={`chip ${showArt ? 'active' : ''}`} onClick={() => setShowArt((v) => !v)}
            title="Show each card's art inside its circle" style={{ marginLeft: 'auto' }}>Card art</button>
          <div className="row" style={{ gap: 6 }}>
            <span className="label-mono" style={{ marginRight: 4 }}>Colors</span>
            {['W', 'U', 'B', 'R', 'G', 'M', 'C'].map((c) =>
            <button key={c} className={`pip ${c}`} onClick={() => toggleColor(c)} style={{ cursor: 'pointer', opacity: colorFilter.size === 0 || colorFilter.has(c) ? 1 : 0.25 }}>{c}</button>
            )}
          </div>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1.1fr 1.3fr 1.1fr', gap: 20, marginTop: 14 }}>
          <div>
            <p className="label-mono" style={{ marginBottom: 6 }}>Price tier (min value)</p>
            <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
              {[
                { v: 0, label: 'All' },
                { v: 1, label: '$1+', sub: 'playable' },
                { v: 5, label: '$5+', sub: 'staples' },
                { v: 20, label: '$20+', sub: 'premium' },
                { v: 50, label: '$50+', sub: 'chase' },
              ].map(t => (
                <button
                  key={t.v}
                  className={`chip ${minValue === t.v ? 'active' : ''}`}
                  onClick={() => setMinValue(t.v)}
                  title={t.sub ? `${t.label} — ${t.sub}` : t.label}
                  style={{ flexDirection: 'column', gap: 0, padding: '5px 9px', lineHeight: 1.2 }}
                >
                  <span>{t.label}</span>
                  {t.sub && <span style={{ fontSize: 7.5, opacity: 0.65, letterSpacing: '0.08em' }}>{t.sub}</span>}
                </button>
              ))}
            </div>
          </div>
          <div>
            <p className="label-mono" style={{ marginBottom: 6 }}>Depth ({matchCount} cards match · showing {Math.min(topN, matchCount)})</p>
            <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
              {[50, 100, 200, 400, 800].map(n => (
                <button
                  key={n}
                  className={`chip ${topN === n ? 'active' : ''}`}
                  onClick={() => setTopN(n)}
                  style={{ padding: '7px 11px' }}
                >
                  Top {n >= 800 ? 'max' : n}
                </button>
              ))}
            </div>
          </div>
          <div>
            <p className="label-mono" style={{ marginBottom: 6 }}>Overlay a deck</p>
            <div style={{ display: 'flex', gap: 6 }}>
              <input className="input" style={{ fontSize: 11, padding: '7px 8px' }} value={deckUrl} onChange={(e) => setDeckUrl(e.target.value)} placeholder="archidekt or moxfield url" />
              <button className="btn sm primary" onClick={loadDeck}>Load</button>
            </div>
          </div>
        </div>
      </div>

      {/* What am I looking at? */}
      <ModeExplainer mode={mode} nodeCount={filteredNodes.length} />

      {/* Canvas + hover */}
      <div className="panel panel-flush" style={{ height: 'calc(100vh - 420px)', minHeight: 520, position: 'relative', overflow: mode === 'hierarchy' || mode === 'set' || mode === 'type' ? 'auto' : 'hidden' }}>
        {mode === 'hierarchy' ?
        <HierarchyMatrix nodes={filteredNodes} openCard={openCard} /> :
        mode === 'set' ?
        <SetConstellation data={data} nodes={nodes} filteredNodes={filteredNodes} openCard={openCard} /> :
        mode === 'type' ?
        <TypeRoster data={data} filteredNodes={filteredNodes} openCard={openCard} /> :

        <div ref={containerRef} style={{ width: '100%', height: '100%', background: 'oklch(0.14 0.012 60)' }}></div>
        }

        {filteredNodes.length === 0 && withData > 0 &&
        <div style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center', color: 'var(--muted)' }}>
            <p>No nodes match your filters.</p>
          </div>
        }
        {withData === 0 &&
        <div style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center', textAlign: 'center' }}>
            <div>
              <div style={{ fontFamily: 'var(--display)', fontSize: 36, color: 'var(--muted)', marginBottom: 12 }}>◇</div>
              <p className="h-display" style={{ fontSize: 20 }}>No card details yet.</p>
              <p className="muted" style={{ fontSize: 12, marginTop: 6 }}>Card details arrive with the daily sync; the network draws itself once they do.</p>
            </div>
          </div>
        }

        {/* Hover card */}
        {hover &&
        <div ref={tipRef} style={{
          position: 'absolute',
          left: Math.min(hover.x + 16, (containerRef.current?.offsetWidth || 1000) - 240),
          top: hover.y + 16, // then clamped once its height is known (tipRef)
          width: 220, pointerEvents: 'none',
          background: 'var(--surface)', border: '1px solid var(--gold)', borderRadius: 4,
          padding: 0, zIndex: 10, overflow: 'hidden'
        }}>
            {hover.d.card?.scry?.img_normal &&
          <img src={hover.d.card.scry.img_normal} onLoad={placeTip} style={{ width: '100%', aspectRatio: '488 / 680', display: 'block' }} alt="" />
          }
            <div style={{ padding: 8 }}>
              <div style={{ fontFamily: 'var(--display)', fontSize: 14, fontWeight: 600 }}>{hover.d.label}</div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--muted)', marginTop: 4 }}>
                <span>×{hover.d.card?.qty ?? hover.d.deckQty ?? '?'}</span>
                <span style={{ color: 'var(--gold)' }}>${(hover.d.card?.value ?? 0).toFixed(2)}</span>
              </div>
              {hover.links &&
            <div style={{ marginTop: 8, borderTop: '1px solid var(--border)', paddingTop: 6 }}>
                  <div className="label-mono" style={{ fontSize: 9, marginBottom: 4 }}>
                    {hover.links.length ? `Linked to ${hover.links.length}` : 'No links: nothing shares enough with it'}
                  </div>
                  {hover.links.map((l) =>
              <div key={l.name} style={{ fontSize: 11, lineHeight: 1.35, marginBottom: 3 }}>
                      <span style={{ fontWeight: 600 }}>{l.name}</span>
                      <span style={{ fontFamily: 'var(--mono)', fontSize: 9.5, color: 'var(--muted)', display: 'block' }}>{l.why}</span>
                    </div>
              )}
                </div>
            }
            </div>
          </div>
        }

        {/* Scatter axis labels */}
        {mode === 'scatter' && filteredNodes.length > 0 &&
        <>
            <div style={{ position: 'absolute', bottom: 40, left: 0, right: 0, display: 'flex', justifyContent: 'space-between', padding: '0 40px', pointerEvents: 'none' }}>
              {[0, 1, 2, 3, 4, 5, 6, 7, 8, 9].map((n) =>
            <span key={n} style={{ fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--muted)', opacity: 0.6 }}>{n === 9 ? '9+' : n}</span>
            )}
            </div>
            <div style={{ position: 'absolute', bottom: 16, left: '50%', transform: 'translateX(-50%)', fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--gold)', letterSpacing: '0.18em', textTransform: 'uppercase', pointerEvents: 'none' }}>
              mana value →
            </div>
            <div style={{ position: 'absolute', top: '50%', left: 8, transform: 'rotate(-90deg) translateX(0)', transformOrigin: 'left top', fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--gold)', letterSpacing: '0.18em', textTransform: 'uppercase', pointerEvents: 'none' }}>
              ← unit price (log)
            </div>
          </>
        }

        {/* Hierarchy mode shape legend */}
        {false && mode === 'hierarchy' && filteredNodes.length > 0 &&
        <div style={{ position: 'absolute', top: 12, left: 12, padding: '10px 12px', background: 'oklch(0.18 0.012 60 / 0.85)', backdropFilter: 'blur(4px)', borderRadius: 4, border: '1px solid var(--border)', pointerEvents: 'none' }}>
            <div className="label-mono" style={{ marginBottom: 6 }}>Shape = type</div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, auto)', gap: '4px 12px', fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--text-2)' }}>
              {[
            ['Creature', 'circle'], ['Land', 'rect'],
            ['Artifact', 'hex'], ['Enchantment', 'diamond'],
            ['Instant', 'tri'], ['Sorcery', 'vee'],
            ['Planeswalker', 'pent'], ['Battle', 'star']].
            map(([t, sh]) =>
            <span key={t}><span style={{ color: 'var(--gold)' }}>◆</span> {t} <span style={{ opacity: 0.5 }}>({sh})</span></span>
            )}
            </div>
          </div>
        }

        {/* Legend */}
        <div style={{ position: 'absolute', bottom: 12, right: 12, padding: '8px 10px', background: 'oklch(0.18 0.012 60 / 0.8)', backdropFilter: 'blur(4px)', borderRadius: 4, border: '1px solid var(--border)', display: 'flex', gap: 12, alignItems: 'center' }}>
          <span className="label-mono" style={{ fontSize: 9 }}>
            {mode === 'affinity' ? 'Edges = shared set/type/color/cmc/price · Hover a card to see why it is linked' :
            mode === 'hierarchy' ? 'Compound rings = color · Node shape = type · Size = total value' :
            mode === 'scatter' ? 'Position fixed: x = mana, y = log price · Jitter for legibility' :
            'Size = total value · Hover for image · Click for details'}
          </span>
        </div>
      </div>
    </div>);

}

function SetConstellation({ data, nodes, filteredNodes, openCard }) {
  const [selected, setSelected] = useStateG(data.sets[0]?.code || null);
  const [hover, setHover] = useStateG(null);
  const [valueMode, setValueMode] = useStateG('total'); // 'total' | 'unit'

  // Build per-set color distribution from nodes with card data
  const setColorMix = useMemoG(() => {
    const out = {};
    for (const n of nodes) {
      if (!out[n.set]) out[n.set] = { W: 0, U: 0, B: 0, R: 0, G: 0, M: 0, C: 0 };
      out[n.set][n.color] += n.value;
    }
    return out;
  }, [nodes]);

  // Per-set stats LIMITED to the active filter (min value / top N / color).
  // Walks the per-printing `entries` of each filtered unique-card node so a card
  // that lives in many sets contributes to each set it actually appears in.
  const filteredSetStats = useMemoG(() => {
    const out = {};
    const unique = {};
    for (const n of filteredNodes) {
      for (const e of (n.entries || [])) {
        const k = e.s;
        if (!out[k]) { out[k] = { qty: 0, value: 0 }; unique[k] = new Set(); }
        out[k].qty += e.q;
        out[k].value += e.q * e.mk;
        unique[k].add(n.name);
      }
    }
    const result = {};
    for (const k of Object.keys(out)) {
      result[k] = { qty: out[k].qty, value: out[k].value, unique: unique[k].size };
    }
    return result;
  }, [filteredNodes]);

  function dominantColor(code) {
    const mix = setColorMix[code];
    if (!mix) return '#7a7770'; // unknown / no card data yet
    let best = 'M',bestV = -1;
    for (const k of Object.keys(mix)) {
      if (mix[k] > bestV) {best = k;bestV = mix[k];}
    }
    return COLOR_FILL[best] || '#7a7770';
  }

  // Sort all sets by FILTERED value; sets not in filter sink to bottom
  const sets = useMemoG(() => {
    return data.sets.slice().sort((a, b) => {
      const va = filteredSetStats[a.code]?.value || 0;
      const vb = filteredSetStats[b.code]?.value || 0;
      return vb - va;
    });
  }, [data, filteredSetStats]);
  const maxV = filteredSetStats[sets[0]?.code]?.value || sets[0]?.value || 1;

  const selectedSet = sets.find((s) => s.code === selected) || sets[0];
  const selectedStats = selectedSet ? (filteredSetStats[selectedSet.code] || { qty: 0, value: 0, unique: 0 }) : null;

  // Top cards for selected set (filtered to the active filter via filteredNodes)
  const filteredNamesInSet = useMemoG(() => {
    if (!selectedSet) return new Set();
    const out = new Set();
    for (const n of filteredNodes) {
      for (const e of (n.entries || [])) {
        if (e.s === selectedSet.code) { out.add(n.name); break; }
      }
    }
    return out;
  }, [filteredNodes, selectedSet]);

  const topCards = useMemoG(() => {
    if (!selectedSet) return [];
    const sortKey = valueMode === 'unit'
      ? (a, b) => b.mk - a.mk
      : (a, b) => b.mk * b.q - a.mk * a.q;
    return data.cards
      .filter((c) => c.s === selectedSet.code && filteredNamesInSet.has(c.n))
      .sort(sortKey)
      .slice(0, 6);
  }, [data, selectedSet, filteredNamesInSet, valueMode]);

  // Color breakdown for selected set
  const selectedMix = selectedSet ? setColorMix[selectedSet.code] : null;
  const mixTotal = selectedMix ? Object.values(selectedMix).reduce((a, b) => a + b, 0) : 0;

  return (
    <div style={{ padding: 18 }}>
      {/* Spotlight FIRST so it's always visible */}
      {selectedSet &&
      <div className="panel" style={{ padding: 20, marginBottom: 16 }}>
          <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 14, flexWrap: 'wrap', gap: 12 }}>
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                {window.SetIcon && <SetIcon code={selectedSet.code} size={36} fallback={false} />}
                <div style={{ fontFamily: 'var(--mono)', fontSize: 28, color: 'var(--gold)', letterSpacing: '0.06em' }}>{selectedSet.code}</div>
              </div>
              <div style={{ fontFamily: 'var(--display)', fontSize: 24, fontWeight: 600, marginTop: 6 }}>{selectedSet.name}</div>
            </div>
            <div style={{ display: 'flex', gap: 14, alignItems: 'flex-start' }}>
              <Stat2 label={`Value (filtered)`} v={`$${selectedStats.value.toLocaleString(undefined, { maximumFractionDigits: 0 })}`} color="var(--gold)" />
              <Stat2 label="Cards" v={selectedStats.qty.toLocaleString()} />
              <Stat2 label="Unique" v={selectedStats.unique.toLocaleString()} />
            </div>
          </div>

          {/* Color mix bar */}
          {selectedMix && mixTotal > 0 &&
        <div style={{ marginBottom: 18 }}>
              <p className="label-mono" style={{ marginBottom: 6 }}>Color identity by value</p>
              <div style={{ display: 'flex', height: 8, borderRadius: 4, overflow: 'hidden', border: '1px solid var(--border)' }}>
                {['W', 'U', 'B', 'R', 'G', 'M', 'C'].map((k) => {
              const pct = mixTotal > 0 ? selectedMix[k] / mixTotal * 100 : 0;
              if (pct < 0.1) return null;
              return (
                <div
                  key={k}
                  style={{ width: `${pct}%`, background: COLOR_FILL[k] }}
                  title={`${COLOR_NAME[k]} — $${selectedMix[k].toFixed(2)}`} />);


            })}
              </div>
            </div>
        }

          {/* Top cards */}
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10, gap: 12, flexWrap: 'wrap' }}>
            <p className="label-mono" style={{ margin: 0 }}>Your top cards from this set</p>
            <div style={{ display: 'flex', gap: 4 }}>
              <button className={`chip ${valueMode === 'total' ? 'active' : ''}`} onClick={() => setValueMode('total')}>Total value</button>
              <button className={`chip ${valueMode === 'unit' ? 'active' : ''}`} onClick={() => setValueMode('unit')}>Unit value</button>
            </div>
          </div>
          {topCards.length === 0 ?
        <p className="muted" style={{ fontSize: 13 }}>No cards from this set in the current filter.</p> :

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)', gap: 10 }}>
              {topCards.map((c) =>
          <SetCardThumb key={`${c.s}-${c.cn}-${c.p}`} c={c} valueMode={valueMode} onClick={() => openCard(c)} />
          )}
            </div>
        }
        </div>
      }

      {/* Bubble cloud BELOW */}
      <p className="label-mono" style={{ marginBottom: 8 }}>
        Pick a set ({sets.filter((s) => (filteredSetStats[s.code]?.value || 0) > 0).length} match filter / {sets.length} total)
      </p>
      <div style={{
        padding: '14px 8px',
        display: 'flex', flexWrap: 'wrap', gap: 5, alignItems: 'center', justifyContent: 'center',
        background: 'oklch(0.14 0.012 60)',
        borderRadius: 4,
        border: '1px solid var(--border)'
      }}>
        {sets.map((s) => {
          const fStats = filteredSetStats[s.code] || { qty: 0, value: 0, unique: 0 };
          const r = fStats.value > 0
            ? Math.max(28, Math.min(80, Math.sqrt(fStats.value / maxV) * 80 + 22))
            : 22;
          const isSel = selected === s.code;
          const dColor = dominantColor(s.code);
          const dimmed = fStats.value === 0;
          return (
            <button
              key={s.code}
              onClick={() => setSelected(s.code)}
              onMouseEnter={(e) => setHover({ s, stats: fStats, x: e.clientX, y: e.clientY })}
              onMouseMove={(e) => setHover({ s, stats: fStats, x: e.clientX, y: e.clientY })}
              onMouseLeave={() => setHover(null)}
              style={{
                width: r, height: r, borderRadius: '50%',
                background: `radial-gradient(circle at 35% 35%, ${dColor}cc, ${dColor}66 70%)`,
                border: isSel ? '2px solid var(--gold)' : '1px solid rgba(0,0,0,0.4)',
                boxShadow: isSel ? '0 0 0 3px rgba(199, 155, 63, 0.22)' : 'none',
                cursor: 'pointer',
                padding: 0,
                display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
                color: dColor === COLOR_FILL.W ? '#22201d' : '#fff',
                transition: 'transform .15s, box-shadow .15s, opacity .15s',
                transform: isSel ? 'scale(1.08)' : 'none',
                flexShrink: 0,
                opacity: dimmed ? 0.3 : 1
              }}>

              <div style={{ fontFamily: 'var(--mono)', fontSize: r > 60 ? 12 : r > 44 ? 10 : 8, fontWeight: 700, letterSpacing: '0.05em', display: 'flex', alignItems: 'center', gap: 4 }}>
                {window.SetIcon && r >= 40 && <SetIcon code={s.code} size={r > 60 ? 16 : 12} variant="white" fallback={false} />}
                <span>{s.code}</span>
              </div>
              {r > 44 &&
              <div style={{ fontFamily: 'var(--mono)', fontSize: r > 60 ? 10 : 8, opacity: 0.85, marginTop: 1 }}>
                  ${fStats.value.toFixed(0)}
                </div>
              }
            </button>);

        })}
      </div>

      {/* Bubble hover tooltip */}
      {hover &&
      <div style={{
        position: 'fixed',
        left: hover.x + 16, top: hover.y + 16,
        padding: '8px 12px',
        background: 'var(--surface)', border: '1px solid var(--gold)', borderRadius: 4,
        zIndex: 200, pointerEvents: 'none', maxWidth: 280,
        boxShadow: '0 8px 24px rgba(0,0,0,0.4)'
      }}>
          <div style={{ fontFamily: 'var(--mono)', fontSize: 12, color: 'var(--gold)', letterSpacing: '0.06em' }}>{hover.s.code}</div>
          <div style={{ fontFamily: 'var(--display)', fontSize: 14, fontWeight: 600, marginTop: 2 }}>{hover.s.name}</div>
          <div style={{ fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--muted)', marginTop: 4 }}>
            {(hover.stats?.qty ?? hover.s.qty)} cards · {(hover.stats?.unique ?? hover.s.unique)} unique · <span style={{ color: 'var(--gold)' }}>${(hover.stats?.value ?? hover.s.value).toFixed(0)}</span>
          </div>
        </div>
      }
    </div>);

}

function Stat2({ label, v, color }) {
  return (
    <div>
      <p className="label-mono">{label}</p>
      <p style={{ fontFamily: 'var(--mono)', fontSize: 16, fontWeight: 500, marginTop: 4, color: color || 'var(--text)' }}>{v}</p>
    </div>);

}

function SetCardThumb({ c, onClick, valueMode = 'total' }) {
  const [scry, setScry] = useStateG(() => window.Scryfall.cached(c.n, c.s, c.cn));
  useEffectG(() => {
    // Reset state when card identity changes so the wrong image isn't shown for a different set/printing
    setScry(window.Scryfall.cached(c.n, c.s, c.cn));
    let dead = false;
    window.Scryfall.collection([{ name: c.n, set: c.s, collector_number: c.cn }]).then((arr) => {
      if (!dead && arr[0]) setScry(arr[0]);
    });
    return () => {dead = true;};
  }, [c.n, c.s, c.cn]);
  const total = (c.mk * c.q).toFixed(2);
  const unit = c.mk.toFixed(2);
  return (
    <button onClick={onClick} style={{ padding: 0, background: 'none', border: 'none', cursor: 'pointer', textAlign: 'left' }}>
      <div style={{ aspectRatio: '488 / 680', background: 'var(--bg-2)', borderRadius: 4, overflow: 'hidden', border: '1px solid var(--border)' }}>
        {scry?.img_normal ?
        <img src={scry.img_normal} alt={c.n} style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }} /> :

        <div style={{ width: '100%', height: '100%', display: 'grid', placeItems: 'center', color: 'var(--muted)', padding: 10, textAlign: 'center', fontFamily: 'var(--mono)', fontSize: 10, background: 'repeating-linear-gradient(135deg, var(--surface-2) 0 8px, var(--surface) 8px 16px)' }}>
            {c.n}
          </div>
        }
      </div>
      <div style={{ marginTop: 6, fontSize: 12, fontWeight: 600, lineHeight: 1.2 }}>{c.n}</div>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontFamily: 'var(--mono)', fontSize: 10, marginTop: 2 }}>
        <span className="muted">×{c.q}</span>
        <span style={{ color: 'var(--gold)' }}>
          ${valueMode === 'unit' ? unit : total}
          {valueMode === 'unit' && c.q > 1 && <span className="muted" style={{ marginLeft: 4 }}>/ea</span>}
        </span>
      </div>
    </button>);

}

function TypeRoster({ data, filteredNodes, openCard }) {
  const TYPES = ['Creature', 'Land', 'Artifact', 'Enchantment', 'Instant', 'Sorcery', 'Planeswalker', 'Battle'];
  const stats = useMemoG(() => {
    const out = {};
    for (const t of TYPES) out[t] = { qty: 0, value: 0, unique: 0, colors: { W: 0, U: 0, B: 0, R: 0, G: 0, M: 0, C: 0 }, candidates: [] };
    for (const n of filteredNodes) {
      const t = TYPES.includes(n.type) ? n.type : null;
      if (!t) continue;
      out[t].qty += n.qty;
      out[t].value += n.value;
      out[t].unique += 1;
      out[t].colors[n.color] += n.value;
      const bestEntry = (n.entries || []).slice().sort((a, b) => b.q * b.mk - a.q * a.mk)[0];
      if (bestEntry) {
        out[t].candidates.push({
          n: n.name, s: bestEntry.s, sn: bestEntry.sn, cn: bestEntry.cn,
          p: bestEntry.p, c: bestEntry.c, l: 'English',
          q: bestEntry.q, mk: bestEntry.mk, lo: 0, mi: 0, pd: 0, fd: '', ld: '',
          total: bestEntry.q * bestEntry.mk
        });
      }
    }
    return out;
  }, [filteredNodes]);

  const totalValue = TYPES.reduce((s, t) => s + stats[t].value, 0);
  const rows = TYPES.
  map((t) => ({ type: t, ...stats[t], top: stats[t].candidates.sort((a, b) => b.total - a.total).slice(0, 4) })).
  sort((a, b) => b.value - a.value);
  const maxValue = rows[0]?.value || 1;

  return (
    <div style={{ padding: 18 }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {rows.map((r, idx) => {
          if (r.qty === 0) return (
            <div key={r.type} style={{
              padding: '14px 18px',
              background: 'var(--surface)',
              border: '1px solid var(--border)',
              borderRadius: 6,
              opacity: 0.35,
              display: 'flex', alignItems: 'center', gap: 14
            }}>
              <span style={{ fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--muted)', width: 22 }}>—</span>
              <TypeGlyph type={r.type} size={22} />
              <div style={{ fontFamily: 'var(--display)', fontSize: 18, fontWeight: 600 }}>{r.type}</div>
              <div className="muted" style={{ fontSize: 11, fontFamily: 'var(--mono)', marginLeft: 'auto' }}>none in this view — widen depth, price tier or colors</div>
            </div>
          );
          const pct = totalValue > 0 ? (r.value / totalValue) * 100 : 0;
          const barPct = (r.value / maxValue) * 100;
          const colorTotal = Object.values(r.colors).reduce((a, b) => a + b, 0) || 1;
          return (
            <div key={r.type} style={{
              background: 'var(--surface)',
              border: '1px solid var(--border)',
              borderRadius: 6,
              position: 'relative',
              overflow: 'hidden',
              display: 'grid',
              gridTemplateColumns: '260px 1fr 440px',
              gap: 20,
              alignItems: 'center',
              padding: '18px 22px'
            }}>
              {/* Left accent rail */}
              <div style={{
                position: 'absolute', left: 0, top: 0, bottom: 0, width: 3,
                background: `linear-gradient(180deg, var(--gold) 0%, var(--copper) 100%)`,
                opacity: 0.6 + (pct / 100) * 0.4
              }}></div>

              {/* Left: rank + name + stats */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
                <span style={{ fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--muted)', width: 18 }}>{String(idx + 1).padStart(2, '0')}</span>
                <TypeGlyph type={r.type} size={32} />
                <div>
                  <div style={{ fontFamily: 'var(--display)', fontSize: 22, fontWeight: 600, lineHeight: 1.1 }}>{r.type}</div>
                  <div style={{ fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--muted)', marginTop: 4 }}>
                    {r.qty.toLocaleString()} cards · {r.unique} unique
                  </div>
                </div>
              </div>

              {/* Middle: value display + bars */}
              <div>
                <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 6 }}>
                  <span style={{ fontFamily: 'var(--display)', fontSize: 26, fontWeight: 600, color: 'var(--gold)' }}>
                    ${r.value.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                  </span>
                  <span style={{ fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--muted)' }}>
                    {pct.toFixed(1)}% of filtered value
                  </span>
                </div>
                <div style={{ position: 'relative', height: 6, background: 'oklch(0.30 0.014 65)', borderRadius: 3, overflow: 'hidden', marginBottom: 8 }}>
                  <div style={{ position: 'absolute', inset: 0, width: `${barPct}%`, background: 'linear-gradient(90deg, var(--gold), var(--copper))', borderRadius: 3 }}></div>
                </div>
                <div style={{ display: 'flex', height: 6, borderRadius: 3, overflow: 'hidden', border: '1px solid var(--border)' }}>
                  {['W','U','B','R','G','M','C'].map(k => {
                    const cp = (r.colors[k] / colorTotal) * 100;
                    if (cp < 0.5) return null;
                    return <div key={k} style={{ width: `${cp}%`, background: COLOR_FILL[k] }} title={`${COLOR_NAME[k]} — $${r.colors[k].toFixed(0)} (${cp.toFixed(1)}%)`} />;
                  })}
                </div>
              </div>

              {/* Right: top thumbnails */}
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 8 }}>
                {r.top.map((c, i) => (
                  <SetCardThumb key={`${r.type}-${c.s}-${c.cn}-${i}`} c={c} valueMode="unit" onClick={() => openCard(c)} />
                ))}
                {Array.from({ length: 4 - r.top.length }).map((_, i) => (
                  <div key={`empty-${i}`} style={{ aspectRatio: '488 / 680', background: 'var(--bg-2)', border: '1px dashed var(--border)', borderRadius: 4, opacity: 0.4 }}></div>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function TypeGlyph({ type, size = 24 }) {
  // Simple SVG glyphs per type — abstract shapes, no copyrighted icons.
  const stroke = 'var(--gold)';
  const sw = Math.max(1, size / 14);
  const c = size / 2;
  const r = size / 2 - 1;
  const glyphs = {
    Creature: <path d={`M ${c - r * 0.6} ${c + r * 0.4} Q ${c} ${c - r} ${c + r * 0.6} ${c + r * 0.4} M ${c - r * 0.4} ${c + r * 0.1} L ${c + r * 0.4} ${c + r * 0.1}`} fill="none" stroke={stroke} strokeWidth={sw} strokeLinecap="round" />,
    Land: <path d={`M 1 ${size - 2} L ${c * 0.6} ${c + 2} L ${c} ${c + r * 0.4} L ${c * 1.4} ${c} L ${size - 1} ${size - 2} Z`} fill="none" stroke={stroke} strokeWidth={sw} strokeLinejoin="round" />,
    Artifact: <g><circle cx={c} cy={c} r={r * 0.55} fill="none" stroke={stroke} strokeWidth={sw} />{[0,1,2,3,4,5].map(i => { const a = (i * Math.PI) / 3; return <line key={i} x1={c + Math.cos(a) * r * 0.55} y1={c + Math.sin(a) * r * 0.55} x2={c + Math.cos(a) * r} y2={c + Math.sin(a) * r} stroke={stroke} strokeWidth={sw} strokeLinecap="round" />; })}</g>,
    Enchantment: <g>{[0,1,2,3,4].map(i => { const a = (i / 5) * Math.PI * 2 - Math.PI / 2; const rIn = r * 0.4, rOut = r * 0.95; const aNext = ((i + 0.5) / 5) * Math.PI * 2 - Math.PI / 2; return [<line key={`o${i}`} x1={c + Math.cos(a) * rOut} y1={c + Math.sin(a) * rOut} x2={c + Math.cos(aNext) * rIn} y2={c + Math.sin(aNext) * rIn} stroke={stroke} strokeWidth={sw} strokeLinecap="round" />, <line key={`i${i}`} x1={c + Math.cos(aNext) * rIn} y1={c + Math.sin(aNext) * rIn} x2={c + Math.cos(((i+1) / 5) * Math.PI * 2 - Math.PI / 2) * rOut} y2={c + Math.sin(((i+1) / 5) * Math.PI * 2 - Math.PI / 2) * rOut} stroke={stroke} strokeWidth={sw} strokeLinecap="round" />]; })}</g>,
    Instant: <path d={`M ${c + r * 0.3} 2 L ${c - r * 0.4} ${c + r * 0.1} L ${c + r * 0.05} ${c + r * 0.1} L ${c - r * 0.3} ${size - 2} L ${c + r * 0.4} ${c - r * 0.1} L ${c - r * 0.05} ${c - r * 0.1} Z`} fill="none" stroke={stroke} strokeWidth={sw} strokeLinejoin="round" />,
    Sorcery: <g><circle cx={c} cy={c} r={r * 0.6} fill="none" stroke={stroke} strokeWidth={sw} />{[0,1,2,3,4,5,6,7].map(i => { const a = (i / 8) * Math.PI * 2; return <line key={i} x1={c + Math.cos(a) * r * 0.7} y1={c + Math.sin(a) * r * 0.7} x2={c + Math.cos(a) * r * 0.95} y2={c + Math.sin(a) * r * 0.95} stroke={stroke} strokeWidth={sw} strokeLinecap="round" />; })}</g>,
    Planeswalker: <g><path d={`M ${c} 2 L ${size - 2} ${c} L ${c} ${size - 2} L 2 ${c} Z`} fill="none" stroke={stroke} strokeWidth={sw} /><circle cx={c} cy={c} r={r * 0.3} fill={stroke} /></g>,
    Battle: <g><path d={`M 2 ${c} L ${c} 2 L ${size - 2} ${c} L ${c} ${size - 2} Z`} fill="none" stroke={stroke} strokeWidth={sw} /><line x1={c} y1={r * 0.4} x2={c} y2={size - r * 0.4} stroke={stroke} strokeWidth={sw} /><line x1={r * 0.4} y1={c} x2={size - r * 0.4} y2={c} stroke={stroke} strokeWidth={sw} /></g>,
  };
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} style={{ flexShrink: 0 }}>
      {glyphs[type] || null}
    </svg>
  );
}

window.GraphView = GraphView;

function HierarchyMatrix({ nodes, openCard }) {
  const COLORS = ['W', 'U', 'B', 'R', 'G', 'M', 'C'];
  const TYPES = ['Creature', 'Land', 'Artifact', 'Enchantment', 'Instant', 'Sorcery', 'Planeswalker', 'Battle'];
  const [hover, setHover] = useStateG(null);

  // Bucket cards into matrix
  const matrix = {};
  const colTotals = {};
  const rowTotals = {};
  let grand = 0;
  for (const c of COLORS) {matrix[c] = {};rowTotals[c] = { qty: 0, value: 0 };}
  for (const t of TYPES) colTotals[t] = { qty: 0, value: 0 };
  for (const c of COLORS) for (const t of TYPES) matrix[c][t] = [];

  for (const n of nodes) {
    if (!matrix[n.color]) continue;
    const bucket = TYPES.includes(n.type) ? n.type : null;
    if (!bucket) continue;
    matrix[n.color][bucket].push(n);
  }

  for (const c of COLORS) {
    for (const t of TYPES) {
      const list = matrix[c][t];
      const sum = list.reduce((s, n) => s + n.value, 0);
      rowTotals[c].qty += list.length;
      rowTotals[c].value += sum;
      colTotals[t].qty += list.length;
      colTotals[t].value += sum;
      grand += sum;
    }
  }

  // Max cell value, for normalizing background heat
  let maxCellValue = 0;
  for (const c of COLORS) for (const t of TYPES) {
    const v = matrix[c][t].reduce((s, n) => s + n.value, 0);
    if (v > maxCellValue) maxCellValue = v;
  }

  return (
    <div style={{ padding: 14, position: 'relative' }}>
      <div style={{
        display: 'grid',
        gridTemplateColumns: '110px repeat(8, 1fr) 110px',
        gap: 4
      }}>
        {/* Header row */}
        <div style={{ padding: 6, fontFamily: 'var(--mono)', fontSize: 9, color: 'var(--muted)', textAlign: 'right', alignSelf: 'end' }}>COLOR ↓ / TYPE →</div>
        {TYPES.map((t) =>
        <div key={t} style={{ textAlign: 'center', padding: 6, borderBottom: '1px solid var(--border)' }}>
            <div style={{ fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--gold)', textTransform: 'uppercase', letterSpacing: '0.1em' }}>{t}</div>
            <div style={{ fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--muted)', marginTop: 2 }}>
              {colTotals[t].qty} · ${colTotals[t].value.toFixed(0)}
            </div>
          </div>
        )}
        <div style={{ textAlign: 'center', padding: 6, borderBottom: '1px solid var(--gold)' }}>
          <div className="label-mono" style={{ color: 'var(--gold)' }}>Row total</div>
        </div>

        {/* Body rows */}
        {COLORS.map((c) =>
        <React.Fragment key={c}>
            <div style={{ padding: 6, display: 'flex', alignItems: 'center', gap: 8, justifyContent: 'flex-end', borderRight: '1px solid var(--border)' }}>
              <span style={{ fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--muted)' }}>
                {rowTotals[c].qty} · <span style={{ color: 'var(--gold)' }}>${rowTotals[c].value.toFixed(0)}</span>
              </span>
              <span className={`pip ${c}`} style={{ width: 22, height: 22, fontSize: 11 }}>{c}</span>
            </div>
            {TYPES.map((t) =>
          <MatrixCell
            key={t}
            cards={matrix[c][t]}
            color={c}
            heat={maxCellValue}
            onHover={setHover}
            onClick={(n) => openCard(n.firstCardObj)} />

          )}
            <div style={{ padding: 6, fontFamily: 'var(--mono)', fontSize: 11, textAlign: 'left', color: 'var(--gold)', borderLeft: '1px solid var(--gold)', alignSelf: 'center' }}>
              ${rowTotals[c].value.toFixed(0)}
            </div>
          </React.Fragment>
        )}

        {/* Footer row */}
        <div style={{ textAlign: 'right', padding: 6, fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--gold)', alignSelf: 'start' }}>Col total →</div>
        {TYPES.map((t) =>
        <div key={t} style={{ textAlign: 'center', padding: 6, fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--gold)', borderTop: '1px solid var(--gold)' }}>
            ${colTotals[t].value.toFixed(0)}
          </div>
        )}
        <div style={{ textAlign: 'center', padding: 6, fontFamily: 'var(--display)', fontSize: 14, color: 'var(--gold)', borderTop: '1px solid var(--gold)', borderLeft: '1px solid var(--gold)' }}>
          ${grand.toFixed(0)}
        </div>
      </div>

      {/* Hover preview */}
      {hover &&
      <div style={{
        position: 'fixed',
        left: hover.x + 16, top: hover.y + 16,
        width: 220, pointerEvents: 'none',
        background: 'var(--surface)', border: '1px solid var(--gold)', borderRadius: 4,
        zIndex: 200, overflow: 'hidden',
        boxShadow: '0 8px 24px rgba(0,0,0,0.4)'
      }}>
          {hover.card.scry?.img_normal &&
        <img src={hover.card.scry.img_normal} style={{ width: '100%', display: 'block' }} alt="" />
        }
          <div style={{ padding: 8 }}>
            <div style={{ fontFamily: 'var(--display)', fontSize: 14, fontWeight: 600 }}>{hover.card.name}</div>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--muted)', marginTop: 4 }}>
              <span>×{hover.card.qty}</span>
              <span style={{ color: 'var(--gold)' }}>${hover.card.value.toFixed(2)}</span>
            </div>
          </div>
        </div>
      }
    </div>);

}

function MatrixCell({ cards, color, heat, onHover, onClick }) {
  const cellValue = cards.reduce((s, c) => s + c.value, 0);
  const intensity = heat > 0 ? Math.min(1, cellValue / heat) : 0;
  const sorted = cards.slice().sort((a, b) => b.value - a.value);
  const display = sorted.slice(0, 30);
  const hidden = sorted.length - display.length;

  const bg = COLOR_FILL[color];
  const alpha = 0.06 + intensity * 0.18;

  return (
    <div style={{
      background: `${bg}${Math.round(alpha * 255).toString(16).padStart(2, '0')}`,
      border: '1px solid var(--border)',
      borderRadius: 4,
      padding: 6,
      minHeight: 90,
      position: 'relative',
      display: 'flex',
      flexDirection: 'column'
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontFamily: 'var(--mono)', fontSize: 9, color: 'var(--muted)', marginBottom: 4 }}>
        <span>{cards.length || '—'}</span>
        <span style={{ color: cellValue > 0 ? 'var(--gold)' : 'var(--muted)' }}>
          {cellValue > 0 ? `$${cellValue.toFixed(0)}` : ''}
        </span>
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 2, alignContent: 'flex-start', flex: 1 }}>
        {display.map((n, i) => {
          const r = Math.max(5, Math.min(22, Math.sqrt(n.value) * 2.4));
          return (
            <button
              key={i}
              onMouseEnter={(e) => onHover({ card: n, x: e.clientX, y: e.clientY })}
              onMouseMove={(e) => onHover({ card: n, x: e.clientX, y: e.clientY })}
              onMouseLeave={() => onHover(null)}
              onClick={() => onClick(n)}
              style={{
                width: r, height: r, borderRadius: '50%',
                background: COLOR_FILL[color],
                border: '1px solid rgba(0,0,0,0.4)',
                padding: 0, cursor: 'pointer', flexShrink: 0
              }} />);


        })}
        {hidden > 0 &&
        <span style={{ fontFamily: 'var(--mono)', fontSize: 9, color: 'var(--muted)', alignSelf: 'center', paddingLeft: 4 }}>+{hidden}</span>
        }
      </div>
    </div>);

}

const MODE_INFO = {
  color: {
    title: 'Color Galaxy',
    body: 'Five mana colors arranged in a pentagon (W top, U·B·R·G clockwise). Multi-color cards sit at the center where all influences meet; colorless cards orbit below. Within each cluster, cards sit on concentric rings — outer rings are lower value, so the priciest cards land closer to their color\'s anchor.'
  },
  type: {
    title: 'Type Roster',
    body: 'Your collection split by card type. Each row is a type with its total value, card count, color-identity mix bar, and your top 4 cards in that type as thumbnails. Read it like a ledger — Creatures dominate, lands carry the value, enchantments are sleepers, etc.'
  },
  set: {
    title: 'Set Constellation',
    body: 'Every set you own as a value-sized bubble — bigger circle = more $. Each bubble is tinted by its dominant color identity. Click a bubble to spotlight that set: see your top 6 cards from it with images, total value, card count, and color breakdown.'
  },
  hierarchy: {
    title: 'Color × Type Matrix',
    body: 'A 7×8 grid of every (color × card-type) bucket. Each cell shows the cards in that bucket as packed dots (dot size = total value), with the count and total $ at the top. Cell background heat scales with cell value, so the biggest pockets of your collection pop visually. Click any dot to open the card; hover to preview.'
  },
  affinity: {
    title: 'Affinity Web',
    body: 'Every card draws weighted similarity edges to its 4 most-related peers. Score combines shared set (+3), type (+2), color (+1.5), similar mana value (+1), similar unit price (+1) and rarity (+0.5); a link needs 3.5. Stronger ties = shorter springs, so cards that share many attributes visibly cluster. Hover a card to light up its links and see what each one shares.'
  },
  scatter: {
    title: 'Mana Value × Price',
    body: 'Fixed position scatter: x-axis is converted mana cost (0–9+), y-axis is log-scaled unit price. Spot the expensive bombs at each curve point, and notice where the value clusters live.'
  },
  deck: {
    title: 'Deck Map',
    body: 'Cards in the loaded deck, grouped by color. Green-bordered nodes are fully owned; dashed red borders are missing from your collection. Pair with the Decks tab for full cost-to-complete numbers.'
  }
};

function ModeExplainer({ mode, nodeCount }) {
  const info = MODE_INFO[mode];
  if (!info) return null;
  return (
    <div style={{ padding: '12px 16px', marginBottom: 8, background: 'oklch(0.20 0.012 60 / 0.6)', borderLeft: '2px solid var(--gold)', borderRadius: 2 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, marginBottom: 4 }}>
        <span className="label-mono" style={{ color: 'var(--gold)' }}>What you're looking at</span>
        <span style={{ fontFamily: 'var(--display)', fontSize: 17, fontWeight: 600, whiteSpace: 'nowrap' }}>{info.title}</span>
        <span style={{ marginLeft: 'auto', fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--muted)' }}>{nodeCount} cards rendered</span>
      </div>
      <p style={{ fontSize: 12.5, color: 'var(--text-2)', lineHeight: 1.55, margin: 0, maxWidth: 1100 }}>{info.body}</p>
    </div>);

}