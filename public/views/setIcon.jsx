// <SetIcon code="DSK" size={20} tinted /> renders a set's SVG icon from Scryfall.
// Falls back to set code text while loading or if the icon can't be fetched.
const { useState: useStateSI, useEffect: useEffectSI } = React;

function SetIcon({ code, size = 18, variant = 'gold', title }) {
  const [tick, setTick] = useStateSI(0);
  useEffectSI(() => {
    if (!window.SetIcons) return;
    const unsub = window.SetIcons.onLoad(() => setTick((t) => t + 1));
    window.SetIcons.loadAll().catch(() => {});  // offline: no icon, and the next view retries
    return unsub;
  }, []);
  const entry = window.SetIcons?.get(code);
  if (!entry?.icon) {
    return (
      <span style={{
        display: 'inline-block',
        fontFamily: 'var(--mono)',
        fontSize: Math.max(8, size * 0.55),
        color: 'var(--text-2)',
        letterSpacing: '0.04em',
      }}>{code}</span>
    );
  }
  // Scryfall ships black SVG glyphs; tint to taste.
  const filterByVariant = {
    gold: 'invert(72%) sepia(46%) saturate(553%) hue-rotate(2deg) brightness(95%) contrast(86%)',
    white: 'brightness(0) invert(1)',
    dark: 'none',
    cream: 'invert(92%) sepia(8%) saturate(160%) hue-rotate(8deg) brightness(98%)',
  };
  return (
    <img
      src={entry.icon}
      alt=""  /* decorative: the set code or name is always written next to it */
      aria-hidden="true"
      title={title || entry.name}
      style={{
        width: size, height: size,
        objectFit: 'contain',
        display: 'inline-block',
        verticalAlign: 'middle',
        filter: filterByVariant[variant] || filterByVariant.gold,
      }}
    />
  );
}

window.SetIcon = SetIcon;
