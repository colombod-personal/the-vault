// Vercel Web Analytics and Speed Insights, bundled into public/analytics.bundle.js by web/build.mjs.
// Both are cookieless and anonymous (privacy notice, "Visitor statistics"); only pages (never the sign-in or consent pages,
// which are rendered by the server without this script) load it.
import { inject } from '@vercel/analytics';
import { injectSpeedInsights } from '@vercel/speed-insights';
import { cleanUrl, measured, routeOf } from './url.mjs';

if (measured(typeof navigator === 'undefined' ? null : navigator)) {
  inject({ mode: 'auto', beforeSend: (event) => ({ ...event, url: cleanUrl(event.url) }) });
  // Without a route every event is filed under "Unknown" (the views live in the hash): name the view, and follow it as the person moves.
  const speed = injectSpeedInsights({ route: routeOf(location), beforeSend: (data) => ({ ...data, url: cleanUrl(data.url) }) });
  window.addEventListener('hashchange', () => speed && speed.setRoute && speed.setRoute(routeOf(location)));
}
