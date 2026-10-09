// Compiles the front end's JSX once, here, instead of in every visitor's browser.
//
// Each file is compiled on its own and the results are joined in the order below, as plain
// scripts: the same globals and load order as the old <script type="text/babel"> tags, so
// nothing in the views changes. The output, public/app.bundle.js, is committed; its header
// holds a hash of the sources, and tests/test_frontend_build.py fails when it is out of date.
//
// Also builds public/analytics.bundle.js (Vercel Web Analytics and Speed Insights) from web/analytics/.
//
//   npm --prefix web ci && npm --prefix web run build     (or: run watch)
import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync, watch } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { transformSync, buildSync } from 'esbuild';

const PUBLIC = join(dirname(fileURLToPath(import.meta.url)), '..', 'public');
export const SOURCES = [
  'tweaks-panel.jsx',
  'views/account.jsx',
  'views/setIcon.jsx',
  'views/dashboard.jsx',
  'views/buckets.jsx',
  'views/tags.jsx',
  'views/scope.jsx',
  'views/browse.jsx',
  'views/sets.jsx',
  'views/deck.jsx',
  'views/deck_change.jsx',
  'views/lab.jsx',
  'views/ideas.jsx',
  'views/buy.jsx',
  'views/valuation.jsx',
  'views/help.jsx',
  'app.jsx',
];

// Vercel Web Analytics and Speed Insights: web/analytics/entry.mjs with its two packages bundled into one classic script.
// The banner holds a hash of the sources and of the installed package versions (tests/test_analytics.py checks it).
export const ANALYTICS_SOURCES = ['analytics/entry.mjs', 'analytics/url.mjs'];
export function analyticsHash() {
  const webDir = dirname(fileURLToPath(import.meta.url));
  const hash = createHash('sha256');
  const text = (file) => readFileSync(join(webDir, file), 'utf8').replace(/\r\n/g, '\n');
  for (const file of ANALYTICS_SOURCES) hash.update(file + '\0' + text(file) + '\0');
  const lock = JSON.parse(text('package-lock.json')).packages;
  for (const name of ['@vercel/analytics', '@vercel/speed-insights']) hash.update(name + '@' + lock['node_modules/' + name].version + '\0');
  return hash.digest('hex');
}

function buildAnalytics() {
  const webDir = dirname(fileURLToPath(import.meta.url));
  buildSync({
    entryPoints: [join(webDir, 'analytics', 'entry.mjs')],
    bundle: true,
    minify: true,
    format: 'iife',
    target: 'es2020',
    outfile: join(PUBLIC, 'analytics.bundle.js'),
    banner: { js: `/* analytics-sha256: ${analyticsHash()} */` },
    logLevel: 'warning',
  });
  console.log('public/analytics.bundle.js: Vercel Web Analytics and Speed Insights');
}

function build() {
  const hash = createHash('sha256');
  const parts = SOURCES.map((file) => {
    // LF whatever the checkout uses (Windows checkouts have CRLF): the same bundle on every machine
    const source = readFileSync(join(PUBLIC, file), 'utf8').replace(/\r\n/g, '\n');
    hash.update(file + '\0' + source + '\0');
    const { code } = transformSync(source, {
      loader: 'jsx', jsx: 'transform', target: 'es2020', minify: true, sourcefile: file, charset: 'utf8',
    });
    return `/* ${file} */\n${code}`;
  });
  const header = `/* The Vault front end. Built by web/build.mjs from ${SOURCES.join(', ')}. Do not edit.\n` +
    `   sources-sha256: ${hash.digest('hex')} */\n`;
  const bundle = header + parts.join(';\n');
  // The files share one global scope: parse the joined result so a name declared in two files
  // fails here, not in the browser.
  transformSync(bundle, { loader: 'js', sourcefile: 'app.bundle.js' });
  writeFileSync(join(PUBLIC, 'app.bundle.js'), bundle);
  console.log(`public/app.bundle.js: ${SOURCES.length} files`);
}

buildAnalytics();
build();
if (process.argv.includes('--watch')) {
  watch(join(dirname(fileURLToPath(import.meta.url)), 'analytics'), () => { try { buildAnalytics(); } catch (e) { console.error(e.message); } });
  for (const file of SOURCES) {
    watch(join(PUBLIC, file), () => { try { build(); } catch (e) { console.error(e.message); } });
  }
  console.log('watching for changes…');
}
