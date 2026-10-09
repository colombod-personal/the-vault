// The scope of the analytics (#130, docs/collections.md decision 5): the Vault, Sets and Value pages show the whole inventory, or one
// bucket, or one tag, or a tag inside a bucket. The choice lives in the address (#/dashboard?bucket=3&tag=trade) and is the same one
// Browse uses, so it follows the person between those pages until they clear it. Every figure is the server's for that selection
// (GET /collection?bucket=&tag= and the same parameters on /sets, /timeline, /history, /valuation, /cards); nothing is added up here.
const { useEffect: useEffectSc, useState: useStateSc } = React;

const scActive = (scope) => !!(scope && (scope.bucket || scope.tag));
const scMoney = (v) => '$' + (v || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

// The summary of the selected bucket / tag, and the client that asks about it. With no selection it is the inventory itself
// (nothing is fetched). A new selection shows nothing until the server has answered, so a figure is never shown under another
// selection's name; a new version of the same selection (after an import or a price update) keeps the old answer meanwhile.
function useScoped(data, scope, enabled) {
  const bucket = enabled && scope.bucket ? scope.bucket : null, tag = enabled && scope.tag ? scope.tag : null;
  const active = !!(bucket || tag);
  const id = `${data ? data.api.base : ''}|${bucket}|${tag}`;
  const key = `${id}|${data ? data.meta.version : ''}`;
  const [state, setState] = useStateSc({ id: null, key: null, data: null, error: null });
  useEffectSc(() => {
    if (!data || !active) return undefined;
    let dead = false;
    data.api.scoped({ bucket, tag }).then(
      (d) => { if (!dead) setState({ id, key, data: d, error: null }); },
      (error) => { if (!dead) setState({ id, key, data: null, error }); });
    return () => { dead = true; };
  }, [key, active]);
  if (!active) return { data, active: false, loading: false, error: null };
  const same = state.id === id;
  return { data: same ? state.data : null, active: true, loading: state.key !== key, error: same && state.key === key ? state.error : null };
}

// "Showing: Trade box, tag trade, 12 cards, $134.20 market value": what the figures below are about, from the server's summary.
function scopeWords(scope, buckets) {
  const parts = [];
  if (scope.bucket) parts.push((buckets.find((b) => b.id === scope.bucket) || { name: `bucket ${scope.bucket}` }).name);
  if (scope.tag) parts.push(`tag ${scope.tag}`);
  return parts.length ? parts.join(', ') : 'the whole inventory';
}

function ScopeBar({ inventory, shown, scope, onScope, loading }) {
  const version = inventory.meta.version;
  const { buckets, loaded: bucketsLoaded } = window.useBuckets(version, true);
  const { tags, loaded: tagsLoaded } = window.useTags(version, true);
  // A bucket or tag that is gone (deleted, renamed, not yours) is dropped from the selection, as in Browse.
  useEffectSc(() => {
    if (scope.bucket && bucketsLoaded && !buckets.some((b) => b.id === scope.bucket)) onScope({ ...scope, bucket: null });
  }, [scope.bucket, buckets, bucketsLoaded]);
  useEffectSc(() => {
    if (scope.tag && tagsLoaded && !tags.some((t) => t.tag === scope.tag)) onScope({ ...scope, tag: null });
  }, [scope.tag, tags, tagsLoaded]);

  const active = scActive(scope);
  const chooseBucket = buckets.length > 1 || !!scope.bucket, chooseTag = tags.length > 0 || !!scope.tag;
  if (!chooseBucket && !chooseTag && !active) return null;  // nothing to choose from: no bar
  const m = shown && shown.meta;
  return (
    <section className="scope-bar" aria-label="What the figures cover">
      <div className="scope-pickers">
      {chooseBucket && <window.BucketBar buckets={buckets} value={scope.bucket || null} totalCopies={inventory.meta.totalQty}
                                   onChange={(bucket) => onScope({ ...scope, bucket })} />}
      {chooseTag && <window.TagBar tags={tags} loaded={tagsLoaded} value={scope.tag || null} quiet
                                   onChange={(tag) => onScope({ ...scope, tag })} />}
      </div>
      <div className="scope-line">
        <p className="scope-showing" role="status" aria-live="polite">
          {loading && !m ? 'Loading the selection…' : m ? (
            <>Showing: <strong>{scopeWords(scope, buckets)}</strong>, {m.totalQty.toLocaleString()} {m.totalQty === 1 ? 'card' : 'cards'}, {scMoney(m.totalMarket)} market value</>
          ) : null}
        </p>
        {active && <button type="button" className="btn xs ghost" onClick={() => onScope({})}>Show the whole inventory</button>}
      </div>
      {active && (
        <p className="muted scope-note">
          Copies, market value and amount paid add up across buckets. Counts of cards, printings and sets do not: a printing kept in two buckets
          counts once in each, but once for the whole inventory.
        </p>
      )}
    </section>
  );
}

// Nothing in the selection: say so and say why, instead of a page of zeros.
function ScopeEmpty({ scope, onScope }) {
  const { buckets } = window.useBuckets(null, true);
  const name = scope.bucket ? (buckets.find((b) => b.id === scope.bucket) || { name: 'That bucket' }).name : '';
  const why = scope.bucket && scope.tag ? `No card tagged ${scope.tag} is in ${name}.`
    : scope.bucket ? `${name} holds no cards.`
    : `You own no card tagged ${scope.tag}. The tag may only be on cards that left your inventory.`;
  return (
    <div className="panel scope-empty" role="status">
      <p className="eyebrow">Nothing here</p>
      <h2 className="h2" style={{ marginTop: 6 }}>No cards in this selection.</h2>
      <p className="muted">{why}</p>
      <button type="button" className="btn" onClick={() => onScope({})}>Show the whole inventory</button>
    </div>
  );
}

// Around the Vault, Sets and Value pages: the scope bar, then the page for the selection (children get that selection's data).
// A shared collection has no bar: buckets and tags are the owner's own.
function ScopeFrame({ inventory, scoped, scope, onScope, viewing, children }) {
  if (viewing) return children(inventory);
  const m = scoped.data && scoped.data.meta;
  return (
    <div>
      <ScopeBar inventory={inventory} shown={scoped.data} scope={scope} onScope={onScope} loading={scoped.loading} />
      {scoped.error ? (
        <div className="panel scope-empty" role="alert">
          <p className="eyebrow">Could not load</p>
          <h2 className="h2" style={{ marginTop: 6 }}>This selection could not be loaded.</h2>
          <p className="muted">{scoped.error.message}</p>
          <button type="button" className="btn" onClick={() => onScope({})}>Show the whole inventory</button>
        </div>
      ) : !scoped.data ? (
        <p className="muted" role="status"><span className="spinner spinner-xs"></span> Loading the selection…</p>
      ) : scoped.active && m.totalQty === 0 ? (
        <ScopeEmpty scope={scope} onScope={onScope} />
      ) : children(scoped.data)}
    </div>
  );
}

Object.assign(window, { useScoped, ScopeBar, ScopeFrame, scopeActive: scActive });
