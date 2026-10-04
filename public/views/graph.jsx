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

// Every colour the server knows: asking for all of them leaves out names with no card data yet.
const GRAPH_COLORS = ['W', 'U', 'B', 'R', 'G', 'M', 'C'];

// A /collection/names row as a graph node. Positions and edges are drawn here; the numbers
// (copies, value, unit price, colour, type, mana value) are the server's.
function graphNode(x, i) {
  return {
    id: 'c_' + i + '_' + x.name.toLowerCase().replace(/[^a-z0-9]/gi, '_'),
    name: x.name,
    qty: x.copies,
    value: x.market_value,
    unit: x.unit_price,
    color: x.color,
    ci: x.color_identity || [],
    cmc: x.cmc ?? 0,
    type: x.type,
    rarity: x.rarity,
    sets: x.sets || [],
    set: (x.sets || [])[0] || '',
    scry: x.image ? { img_normal: x.image.normal, img_small: x.image.small, artist: x.image.artist } : null,
  };
}

// The colour key of a card's colour identity (W, U, B, R, G; M multicolour; C colourless), for
// placing deck cards that aren't among the nodes.
const graphColorKey = (ci) => (!ci || !ci.length ? 'C' : ci.length === 1 ? ci[0] : 'M');

// The card's art, cropped (Scryfall art_crop). Older cached cards only kept the normal image,
// whose URL differs only in the size segment.
function artUrl(scry) {
  if (!scry) return null;
  return scry.img_art || (scry.img_normal ? scry.img_normal.replace('/normal/', '/art_crop/') : null);
}

const ART_ASPECT = 626 / 457; // Scryfall's art_crop: a card is drawn as a tile of this shape, never cut
const MAX_ART_NODES = 60; // art for the most valuable cards only: a big graph would download hundreds of images
const MAX_LINKS_SHOWN = 5; // a popular card can be picked by many peers: list the strongest
// No hover on touch screens: the graph previews a card on the first tap (see the tap handler).
const TOUCH = !!(window.matchMedia && window.matchMedia('(hover: none)').matches);
const ART_MODES = new Set(['color', 'affinity', 'scatter', 'deck']); // the modes drawn as a network
const SAME = { set: 'a shared set', type: 'same type', color: 'same color group', rarity: 'same rarity', cmc: 'similar mana value', price: 'similar price' };

function GraphView({ data, openCard }) {
  const api = data.api;
  const containerRef = useRefG(null);
  const cyRef = useRefG(null);
  const [mode, setMode] = useStateG('color'); // color | set | type | scatter | affinity | hierarchy | deck
  const [topN, setTopN] = useStateG(200);
  const [minValue, setMinValue] = useStateG(1);
  const [colorFilter, setColorFilter] = useStateG(new Set()); // empty = all
  const [hover, setHover] = useStateG(null);
  // The input last used on the graph (kept across rebuilds); the legend names its gesture.
  const lastPointer = useRefG(TOUCH ? 'touch' : 'mouse');
  const [touchUI, setTouchUI] = useStateG(TOUCH);
  const [showArt, setShowArt] = useStateG(true); // each card drawn as its whole art
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

  // The nodes: the server's top names by value, filtered there by colour and price (min value),
  // as many as the depth asks for (GET /collection/names).
  const at = [api.base, data.meta.version];
  const colors = colorFilter.size ? GRAPH_COLORS.filter((c) => colorFilter.has(c)) : GRAPH_COLORS;
  const filters = { colors, min_value: minValue || null };
  const filtersKey = colors.join(',') + '|' + minValue;
  const res = window.useVaultQuery(() => api.names({ ...filters, sort: '-value' }, topN), [...at, filtersKey, topN]);
  const filteredNodes = useMemoG(() => (res.data ? res.data.items.map(graphNode) : []), [res.data]);
  // How many names match the colour + price filter before the top-N depth cap (the server's total)
  const matchCount = res.data ? res.data.total : 0;
  // Names with card data / every name (names without card data have type "unknown")
  const counts = window.useVaultQuery(async () => {
    const [every, unknown] = await Promise.all([api.names({}, 1), api.names({ type: 'unknown' }, 1)]);
    return { names: every.total, withData: every.total - unknown.total };
  }, at).data || { names: 0, withData: 0 };
  const withData = counts.withData; // unique names whose card data the server has

  // Open a card name's most valuable printing (nodes are names, not printings).
  const openName = (name) => api.topPrinting(name).then((c) => c && openCard(c)).catch(() => {});

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
      // What you own of it comes from the server's coverage; colours from the cards' data.
      const [cov, scry] = await Promise.all([
        window.VaultApi.deckCoverage(deckListText(d.cards)),
        window.Scryfall.collection(d.cards.map((c) => ({ name: c.name, set: c.set, collector_number: c.collector_number }))),
      ]);
      const lines = coverageFor(d.cards, cov.cards);
      const rows = d.cards.map((c, i) => ({ ...c, scry: scry[i], owned: lines[i] ? lines[i].have : 0,
        missing: lines[i] ? lines[i].missing : c.qty, unit: lines[i] ? lines[i].unit_price : null }));
      setDeck({ title: d.title, rows });
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
    const artIds = new Set(showArt ? [...filteredNodes].sort((a, b) => b.value - a.value).slice(0, MAX_ART_NODES).map((n) => n.id) : []);

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
          ...(artIds.has(n.id) && artUrl(n.scry) ? { art: artUrl(n.scry), artW: Math.round(radius * ART_ASPECT) } : {})
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
        for (const code of n.sets) (setIdx[code] = setIdx[code] || []).push(i);
        (typeIdx[n.type] = typeIdx[n.type] || []).push(i);
        (colorIdx[n.color] = colorIdx[n.color] || []).push(i);
      });
      // Score and the shared traits behind it (shown when hovering a card).
      function sim(a, b) {
        let s = 0;
        const why = [];
        if (a.sets.some((code) => b.sets.includes(code))) {s += 3.0;why.push('set');}
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
        ...a.sets.flatMap((code) => setIdx[code] || []),
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
    } else if (mode === 'deck' && deck) {
      // Show only deck cards; what you own of each is the server's coverage (loadDeck).
      const deckRows = deck.rows.map((r) => {
        const key = r.name.toLowerCase().trim();
        const node = filteredNodes.find((n) => n.name.toLowerCase().trim() === key);
        const color = node ? node.color : r.scry ? graphColorKey(r.scry.color_identity) : '?';
        return { ...r, node, color };
      });
      // Group by color (from the card data) else by 'unknown'
      const groups = { W: [], U: [], B: [], R: [], G: [], M: [], C: [], '?': [] };
      for (const r of deckRows) groups[r.color in groups ? r.color : '?'].push(r);
      let deckArt = 0;
      for (const k of Object.keys(groups)) {
        if (groups[k].length === 0) continue;
        const pid = 'p_' + k;
        parents.add(pid);
        elements.push({ data: { id: pid, label: COLOR_NAME[k] || 'Unknown', isParent: true } });
        for (const r of groups[k]) {
          const id = 'dk_' + r.name.replace(/[^a-z0-9]/gi, '_');
          const radius = Math.max(8, Math.min(60, Math.sqrt(r.unit || 1) * 5 + 8));
          const art = showArt && deckArt < MAX_ART_NODES ? artUrl(r.node?.scry || r.scry) : null;
          if (art) deckArt++;
          elements.push({
            data: {
              id, label: r.name, parent: pid,
              radius,
              color: COLOR_FILL[r.color] || '#444',
              inDeck: r.missing > 0 ? 'missing' : 'owned',
              card: r.node || { name: r.name, scry: r.scry, qty: r.owned, value: null },
              deckQty: r.qty,
              owned: r.owned,
              ...(art ? { art, artW: Math.round(radius * ART_ASPECT) } : {})
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
        // The card's whole art (never cut: a tile in the art's own shape); its color identity is the border.
        selector: 'node[art]',
        style: {
          'shape': 'round-rectangle',
          'width': 'data(artW)',
          'background-image': 'data(art)',
          'background-fit': 'contain',
          'background-clip': 'node',
          'background-image-crossorigin': 'anonymous',
          'border-color': 'data(color)',
          'border-width': 2.5
        }
      },
      {
        selector: 'node[inDeck = "yes"]',
        style: { 'outline-color': '#f4d35e', 'outline-width': 3, 'outline-offset': 1 }
      },
      {
        selector: 'node[inDeck = "missing"]',
        style: { 'outline-color': '#d04d35', 'outline-width': 3, 'outline-offset': 1, 'outline-style': 'dashed' }
      },
      {
        selector: 'node[inDeck = "owned"]',
        style: { 'outline-color': '#3f8a5b', 'outline-width': 2, 'outline-offset': 1 }
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

    // Touch has no hover: the first tap on a card shows what hovering shows (its image, and in the
    // affinity web its links), a second tap on the same card opens it. Decided by the input that
    // was used (a finger on a touch laptop too), falling back to the screen's primary pointer.
    const notePointer = (e) => {
      lastPointer.current = e.type === 'touchstart' || e.pointerType === 'touch' ? 'touch' : 'mouse';
      setTouchUI(lastPointer.current === 'touch');
    };
    const host = cy.container();
    host.addEventListener('pointerdown', notePointer, true);
    host.addEventListener('pointermove', notePointer, true);
    host.addEventListener('touchstart', notePointer, { capture: true, passive: true });
    const isTouch = () => lastPointer.current === 'touch';
    let previewed = null;
    const clearPreview = () => {
      cy.elements().removeClass('faded linked');
      setHover(null);
    };
    const showPreview = (node) => {
      const d = node.data();
      const pos = node.renderedPosition();
      let links = null;
      if (mode === 'affinity') {
        const edges = node.connectedEdges();
        cy.elements().removeClass('faded linked');
        cy.elements().not(node.closedNeighborhood()).addClass('faded');
        edges.addClass('linked');
        links = edges.map((e) => ({
          name: (e.source().id() === node.id() ? e.target() : e.source()).data('label'),
          why: e.data('why'),
          weight: e.data('weight')
        })).sort((x, y) => y.weight - x.weight);
      }
      setHover({ d, x: pos.x, y: pos.y, links });
    };
    cy.on('tap', 'node', (evt) => {
      const d = evt.target.data();
      if (d.isParent || d.isAnchor) return;
      if (isTouch() && previewed !== evt.target.id()) {
        previewed = evt.target.id();
        showPreview(evt.target);
        return;
      }
      previewed = null;
      if (d.card?.name) openName(d.card.name);
    });
    cy.on('tap', (evt) => {
      if (evt.target === cy) { previewed = null; clearPreview(); }
    });
    cy.on('mouseover', 'node', (evt) => {
      const d = evt.target.data();
      if (d.isParent || d.isAnchor || isTouch()) return;
      showPreview(evt.target);
    });
    cy.on('mouseout', 'node', () => {
      if (!isTouch()) clearPreview();
    });
    cy.on('viewport', () => {
      previewed = null;
      clearPreview();
    });

    // Always fit to viewport after layout completes
    cy.one('layoutstop', () => {
      cy.fit(undefined, 40);
    });

    cyRef.current = cy;
    return () => {
      host.removeEventListener('pointerdown', notePointer, true);
      host.removeEventListener('pointermove', notePointer, true);
      host.removeEventListener('touchstart', notePointer, { capture: true });
      cy.destroy();cyRef.current = null;
    };
  }, [filteredNodes, mode, deck, showArt]);

  // A rebuilt graph starts with no card previewed (a tap preview otherwise stays up).
  useEffectG(() => setHover(null), [filteredNodes, mode, deck, showArt]);

  return (
    <div data-screen-label="06 Graph">
      <div className="page-head" style={{ marginBottom: 20, display: 'flex', justifyContent: 'space-between', alignItems: 'end' }}>
        <div>
          <p className="eyebrow">The atlas</p>
          <h1 className="h1" style={{ marginTop: 6 }}>Your collection as a network.</h1>
        </div>
        <div style={{ textAlign: 'right' }}>
          <p className="label-mono">Cards with data</p>
          <p style={{ fontFamily: 'var(--mono)', fontSize: 13, marginTop: 4 }}>
            <span style={{ color: 'var(--gold)' }}>{withData.toLocaleString()}</span>
            <span className="muted"> / {counts.names.toLocaleString()} unique names</span>
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
          {ART_MODES.has(mode) ?
          <button className={`chip ${showArt ? 'active' : ''}`} aria-pressed={showArt} onClick={() => setShowArt((v) => !v)}
          title="Show each card as its art" style={{ marginLeft: 'auto' }}>Card art</button> :
          <span style={{ marginLeft: 'auto' }} />}
          <div className="row" style={{ gap: 6 }}>
            <span className="label-mono" style={{ marginRight: 4 }}>Colors</span>
            {['W', 'U', 'B', 'R', 'G', 'M', 'C'].map((c) =>
            <button key={c} className={`pip ${c}`} onClick={() => toggleColor(c)} style={{ cursor: 'pointer', opacity: colorFilter.size === 0 || colorFilter.has(c) ? 1 : 0.25 }}>{c}</button>
            )}
          </div>
        </div>
        <div className="m-stack" style={{ display: 'grid', gridTemplateColumns: '1.1fr 1.3fr 1.1fr', gap: 20, marginTop: 14 }}>
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
      <div className="panel panel-flush graph-stage" style={{ height: 'calc(100vh - 420px)', minHeight: 520, position: 'relative', overflow: mode === 'hierarchy' || mode === 'set' || mode === 'type' ? 'auto' : 'hidden' }}>
        {mode === 'hierarchy' ?
        <HierarchyMatrix data={data} nodes={filteredNodes} openName={openName} /> :
        mode === 'set' ?
        <SetConstellation data={data} filteredNodes={filteredNodes} openCard={openCard} /> :
        mode === 'type' ?
        <TypeRoster data={data} filters={filters} filtersKey={filtersKey} openName={openName} /> :

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
            {hover.links && artUrl(hover.d.card?.scry) ?
          // With its links listed, the card shows its art crop (credited below) so it fits the graph.
          <img src={artUrl(hover.d.card.scry)} onLoad={placeTip} style={{ width: '100%', aspectRatio: '626 / 457', objectFit: 'contain', display: 'block' }} alt="" /> :
          hover.d.card?.scry?.img_normal &&
          <img src={hover.d.card.scry.img_normal} onLoad={placeTip} style={{ width: '100%', aspectRatio: '488 / 680', display: 'block' }} alt="" />
          }
            <div style={{ padding: 8 }}>
              <div style={{ fontFamily: 'var(--display)', fontSize: 14, fontWeight: 600 }}>{hover.d.label}</div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--muted)', marginTop: 4 }}>
                <span>×{hover.d.card?.qty ?? hover.d.deckQty ?? '?'}</span>
                <span style={{ color: 'var(--gold)' }}>{hover.d.card?.value != null ? `$${hover.d.card.value.toFixed(2)}` : ''}</span>
              </div>
              {hover.d.card?.scry?.artist &&
            <div style={{ fontSize: 10.5, color: 'var(--text-2)', marginTop: 4 }}>Art by {hover.d.card.scry.artist}</div>
            }
              {hover.links &&
            <div style={{ marginTop: 8, borderTop: '1px solid var(--border)', paddingTop: 6 }}>
                  <div className="label-mono" style={{ fontSize: 9, marginBottom: 4 }}>
                    {hover.links.length ? `Linked to ${hover.links.length}` : 'No links: nothing shares enough with it'}
                  </div>
                  {hover.links.slice(0, MAX_LINKS_SHOWN).map((l) =>
              // One line each (cut with an ellipsis), so five links always fit the graph's height.
              <div key={l.name} style={{ fontSize: 11, lineHeight: 1.35, marginBottom: 3, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      <span style={{ fontWeight: 600 }}>{l.name}</span>
                      <span style={{ fontFamily: 'var(--mono)', fontSize: 9.5, color: 'var(--text-2)', display: 'block', overflow: 'hidden', textOverflow: 'ellipsis' }}>{l.why}</span>
                    </div>
              )}
                  {hover.links.length > MAX_LINKS_SHOWN &&
              <div style={{ fontFamily: 'var(--mono)', fontSize: 9.5, color: 'var(--text-2)' }}>
                      and {hover.links.length - MAX_LINKS_SHOWN} more (the lit lines show them all)
                    </div>
              }
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
        <div className="graph-legend" style={{ position: 'absolute', bottom: 12, right: 12, padding: '8px 10px', background: 'oklch(0.18 0.012 60 / 0.8)', backdropFilter: 'blur(4px)', borderRadius: 4, border: '1px solid var(--border)', display: 'flex', gap: 12, alignItems: 'center' }}>
          <span className="label-mono" style={{ fontSize: 9 }}>
            {mode === 'affinity' ? `Edges = shared set/type/color/cmc/price/rarity · ${touchUI ? 'Tap' : 'Hover'} a card to see why it is linked` :
            mode === 'hierarchy' ? 'Compound rings = color · Node shape = type · Size = total value' :
            mode === 'scatter' ? 'Position fixed: x = mana, y = log price · Jitter for legibility' :
            touchUI ? 'Size = total value · Tap for image · Tap again for details' : 'Size = total value · Hover for image · Click for details'}
            {showArt && ART_MODES.has(mode) && ` · Art by the credited artists (${touchUI ? 'tap' : 'hover'} a card for its artist)`}
          </span>
        </div>
      </div>
    </div>);

}

function SetConstellation({ data, filteredNodes, openCard }) {
  const api = data.api;
  const [selected, setSelected] = useStateG(null);
  const [hover, setHover] = useStateG(null);
  const [valueMode, setValueMode] = useStateG('total'); // 'total' | 'unit'

  // Every set with its value, copies and colour mix, from the server (most valuable first).
  const all = (window.useVaultQuery(() => api.sets(), [api.base, data.meta.version]).data || { items: [] }).items;
  // Sets holding a name in the current filter (colour, price tier, depth) come first, lit up.
  const matching = useMemoG(() => new Set(filteredNodes.flatMap((n) => n.sets)), [filteredNodes]);
  const sets = useMemoG(() => [...all.filter((s) => matching.has(s.code)), ...all.filter((s) => !matching.has(s.code))],
    [all, matching]);
  const maxV = all[0]?.value || 1;

  // The colour with the most copies in a set (the server's per-set colour mix)
  function dominantColor(s) {
    let best = null, bestV = 0;
    for (const k of Object.keys(COLOR_FILL)) {
      if ((s.colors[k] || 0) > bestV) { best = k; bestV = s.colors[k]; }
    }
    return best ? COLOR_FILL[best] : '#7a7770'; // unknown / no card data yet
  }

  const selectedSet = sets.find((s) => s.code === selected) || sets[0];

  // Top printings of the selected set: the server's most valuable; "Unit value" orders the
  // set's 60 most valuable printings by their price each.
  const top = window.useVaultQuery(() => (selectedSet ? api.cards({ set: selectedSet.code, sort: '-value', limit: valueMode === 'unit' ? 60 : 6 }) : null),
    [api.base, data.meta.version, selectedSet?.code, valueMode]).data;
  const topCards = top ? (valueMode === 'unit' ? top.items.slice().sort((a, b) => b.mk - a.mk) : top.items).slice(0, 6) : [];

  // Color breakdown for selected set: copies by colour identity
  const selectedMix = selectedSet ? selectedSet.colors : null;
  const mixTotal = selectedMix ? Object.keys(COLOR_FILL).reduce((a, k) => a + (selectedMix[k] || 0), 0) : 0;

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
              <Stat2 label="Value" v={`$${selectedSet.value.toLocaleString(undefined, { maximumFractionDigits: 0 })}`} color="var(--gold)" />
              <Stat2 label="Cards" v={selectedSet.qty.toLocaleString()} />
              <Stat2 label="Unique" v={selectedSet.unique.toLocaleString()} />
            </div>
          </div>

          {/* Color mix bar */}
          {selectedMix && mixTotal > 0 &&
        <div style={{ marginBottom: 18 }}>
              <p className="label-mono" style={{ marginBottom: 6 }}>Color identity by cards</p>
              <div style={{ display: 'flex', height: 8, borderRadius: 4, overflow: 'hidden', border: '1px solid var(--border)' }}>
                {['W', 'U', 'B', 'R', 'G', 'M', 'C'].map((k) => {
              const pct = mixTotal > 0 ? (selectedMix[k] || 0) / mixTotal * 100 : 0;
              if (pct < 0.1) return null;
              return (
                <div
                  key={k}
                  style={{ width: `${pct}%`, background: COLOR_FILL[k] }}
                  title={`${COLOR_NAME[k]} — ${selectedMix[k].toLocaleString()} cards`} />);


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
        <p className="muted" style={{ fontSize: 13 }}>No cards from this set.</p> :

        <div className="m-3col" style={{ display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)', gap: 10 }}>
              {topCards.map((c) =>
          <SetCardThumb key={`${c.s}-${c.cn}-${c.p}`} c={c} valueMode={valueMode} onClick={() => openCard(c)} />
          )}
            </div>
        }
        </div>
      }

      {/* Bubble cloud BELOW */}
      <p className="label-mono" style={{ marginBottom: 8 }}>
        Pick a set ({matching.size} match filter / {sets.length} total)
      </p>
      <div style={{
        padding: '14px 8px',
        display: 'flex', flexWrap: 'wrap', gap: 5, alignItems: 'center', justifyContent: 'center',
        background: 'oklch(0.14 0.012 60)',
        borderRadius: 4,
        border: '1px solid var(--border)'
      }}>
        {sets.map((s) => {
          const r = s.value > 0
            ? Math.max(28, Math.min(80, Math.sqrt(s.value / maxV) * 80 + 22))
            : 22;
          const isSel = selectedSet && selectedSet.code === s.code;
          const dColor = dominantColor(s);
          const dimmed = !matching.has(s.code);
          return (
            <button
              key={s.code}
              onClick={() => setSelected(s.code)}
              aria-pressed={!!isSel}
              onMouseEnter={(e) => setHover({ s, x: e.clientX, y: e.clientY })}
              onMouseMove={(e) => setHover({ s, x: e.clientX, y: e.clientY })}
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
                  ${s.value.toFixed(0)}
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
            {hover.s.qty} cards · {hover.s.unique} unique · <span style={{ color: 'var(--gold)' }}>${hover.s.value.toFixed(0)}</span>
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
  const [scry, setScry] = useStateG(() => c.scry || window.Scryfall.cached(c.n, c.s, c.cn));
  useEffectG(() => {
    // Reset state when card identity changes so the wrong image isn't shown for a different set/printing
    setScry(c.scry || window.Scryfall.cached(c.n, c.s, c.cn));
    if (c.scry) return;
    let dead = false;
    window.Scryfall.collection([{ name: c.n, set: c.s, collector_number: c.cn }]).then((arr) => {
      if (!dead && arr[0]) setScry(arr[0]);
    });
    return () => {dead = true;};
  }, [c.n, c.s, c.cn]);
  const total = (c.v != null ? c.v : c.mk * c.q).toFixed(2);
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

function TypeRoster({ data, filters, filtersKey, openName }) {
  const api = data.api;
  const TYPES = ['Creature', 'Land', 'Artifact', 'Enchantment', 'Instant', 'Sorcery', 'Planeswalker', 'Battle'];
  const at = [api.base, data.meta.version];
  // Copies and value per type, and each type's colour mix (colour × type), from the server.
  const breakdowns = window.useVaultQuery(() => api.breakdowns(), at).data;
  // Each type's most valuable names in the current colour and price filter, and how many there are.
  const tops = window.useVaultQuery(() => Promise.all(TYPES.map((t) => api.names({ ...filters, type: t, sort: '-value' }, 4))),
    [...at, filtersKey]).data;

  const totalValue = breakdowns ? breakdowns.totals.market_value : 0;
  const rows = TYPES.map((t, i) => {
    const b = (breakdowns?.types || []).find((x) => x.key === t) || { copies: 0, market_value: 0 };
    const colors = {};
    for (const cell of breakdowns?.matrix || []) if (cell.type === t) colors[cell.color] = cell.market_value;
    const names = tops ? tops[i] : null;
    return {
      type: t, qty: b.copies, value: b.market_value, colors, unique: names ? names.total : 0,
      top: (names ? names.items : []).map((x) => ({
        n: x.name, s: x.sets.length === 1 ? x.sets[0] : '', cn: '', q: x.copies, mk: x.unit_price, v: x.market_value,
        scry: x.image ? { img_normal: x.image.normal, img_small: x.image.small, artist: x.image.artist } : null,
      })),
    };
  }).sort((a, b) => b.value - a.value);
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
              <div className="muted" style={{ fontSize: 11, fontFamily: 'var(--mono)', marginLeft: 'auto' }}>none in your collection</div>
            </div>
          );
          const pct = totalValue > 0 ? (r.value / totalValue) * 100 : 0;
          const barPct = (r.value / maxValue) * 100;
          const colorTotal = Object.keys(COLOR_FILL).reduce((a, k) => a + (r.colors[k] || 0), 0) || 1;
          return (
            <div key={r.type} className="m-stack" style={{
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
                    {pct.toFixed(1)}% of collection value
                  </span>
                </div>
                <div style={{ position: 'relative', height: 6, background: 'oklch(0.30 0.014 65)', borderRadius: 3, overflow: 'hidden', marginBottom: 8 }}>
                  <div style={{ position: 'absolute', inset: 0, width: `${barPct}%`, background: 'linear-gradient(90deg, var(--gold), var(--copper))', borderRadius: 3 }}></div>
                </div>
                <div style={{ display: 'flex', height: 6, borderRadius: 3, overflow: 'hidden', border: '1px solid var(--border)' }}>
                  {['W','U','B','R','G','M','C'].map(k => {
                    const cp = ((r.colors[k] || 0) / colorTotal) * 100;
                    if (cp < 0.5) return null;
                    return <div key={k} style={{ width: `${cp}%`, background: COLOR_FILL[k] }} title={`${COLOR_NAME[k]} — $${r.colors[k].toFixed(0)} (${cp.toFixed(1)}%)`} />;
                  })}
                </div>
              </div>

              {/* Right: top thumbnails */}
              <div className="m-2col" style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 8 }}>
                {r.top.map((c, i) => (
                  <SetCardThumb key={`${r.type}-${c.n}-${i}`} c={c} valueMode="unit" onClick={() => openName(c.n)} />
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

function HierarchyMatrix({ data, nodes, openName }) {
  const api = data.api;
  const COLORS = ['W', 'U', 'B', 'R', 'G', 'M', 'C'];
  const TYPES = ['Creature', 'Land', 'Artifact', 'Enchantment', 'Instant', 'Sorcery', 'Planeswalker', 'Battle'];
  const [hover, setHover] = useStateG(null);
  // Copies and value per colour × type, per colour and per type: the server's (whole collection)
  const breakdowns = window.useVaultQuery(() => api.breakdowns(), [api.base, data.meta.version]).data;
  const zero = { copies: 0, market_value: 0 };
  const cellOf = (c, t) => (breakdowns?.matrix || []).find((x) => x.color === c && x.type === t) || zero;
  const rowTotals = {}, colTotals = {};
  for (const c of COLORS) rowTotals[c] = (breakdowns?.colors || []).find((x) => x.key === c) || zero;
  for (const t of TYPES) colTotals[t] = (breakdowns?.types || []).find((x) => x.key === t) || zero;
  const grand = breakdowns ? breakdowns.totals.market_value : 0;

  // The dots: the nodes in view (top names by value, in the current filter), placed in their cell
  const matrix = {};
  for (const c of COLORS) { matrix[c] = {}; for (const t of TYPES) matrix[c][t] = []; }
  for (const n of nodes) if (matrix[n.color] && matrix[n.color][n.type]) matrix[n.color][n.type].push(n);

  // Max cell value, for normalizing background heat
  let maxCellValue = 0;
  for (const c of COLORS) for (const t of TYPES) maxCellValue = Math.max(maxCellValue, cellOf(c, t).market_value);

  return (
    <div style={{ padding: 14, position: 'relative' }}>
      <div className="m-matrix" style={{
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
              {colTotals[t].copies} · ${colTotals[t].market_value.toFixed(0)}
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
                {rowTotals[c].copies} · <span style={{ color: 'var(--gold)' }}>${rowTotals[c].market_value.toFixed(0)}</span>
              </span>
              <span className={`pip ${c}`} style={{ width: 22, height: 22, fontSize: 11 }}>{c}</span>
            </div>
            {TYPES.map((t) =>
          <MatrixCell
            key={t}
            cards={matrix[c][t]}
            cell={cellOf(c, t)}
            color={c}
            heat={maxCellValue}
            onHover={setHover}
            onClick={(n) => openName(n.name)} />

          )}
            <div style={{ padding: 6, fontFamily: 'var(--mono)', fontSize: 11, textAlign: 'left', color: 'var(--gold)', borderLeft: '1px solid var(--gold)', alignSelf: 'center' }}>
              ${rowTotals[c].market_value.toFixed(0)}
            </div>
          </React.Fragment>
        )}

        {/* Footer row */}
        <div style={{ textAlign: 'right', padding: 6, fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--gold)', alignSelf: 'start' }}>Col total →</div>
        {TYPES.map((t) =>
        <div key={t} style={{ textAlign: 'center', padding: 6, fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--gold)', borderTop: '1px solid var(--gold)' }}>
            ${colTotals[t].market_value.toFixed(0)}
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

// One colour × type cell: the server's copies and value for it, and the nodes in view as dots
// (they come most valuable first).
function MatrixCell({ cards, cell, color, heat, onHover, onClick }) {
  const cellValue = cell.market_value;
  const intensity = heat > 0 ? Math.min(1, cellValue / heat) : 0;
  const display = cards.slice(0, 30);
  const hidden = cards.length - display.length;

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
        <span>{cell.copies || '—'}</span>
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
    body: 'Every card draws weighted similarity edges to its 4 most-related peers. Score combines shared set (+3), type (+2), color (+1.5), similar mana value (+1), similar unit price (+1) and rarity (+0.5); a link needs 3.5. Stronger ties = shorter springs, so cards that share many attributes visibly cluster. Hover (or tap) a card to light up its links and see what each one shares.'
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