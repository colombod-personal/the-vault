// Vercel Web Analytics and Speed Insights, bundled into public/analytics.bundle.js by web/build.mjs.
// Both are cookieless and anonymous (privacy notice, "Visitor statistics"); only pages (never the sign-in or consent pages,
// which are rendered by the server without this script) load it.
import { inject } from '@vercel/analytics';
import { injectSpeedInsights } from '@vercel/speed-insights';
import { cleanUrl, measured } from './url.mjs';

if (measured(typeof navigator === 'undefined' ? null : navigator)) {
  inject({ mode: 'auto', beforeSend: (event) => ({ ...event, url: cleanUrl(event.url) }) });
  injectSpeedInsights({ beforeSend: (data) => ({ ...data, url: cleanUrl(data.url) }) });
}
