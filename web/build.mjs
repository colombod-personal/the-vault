// Compiles the front end's JSX once, here, instead of in every visitor's browser.
//
// Each file is compiled on its own and the results are joined in the order below, as plain
// scripts: the same globals and load order as the old <script type="text/babel"> tags, so
// nothing in the views changes. The output, public/app.bundle.js, is committed; its header
// holds a hash of the sources, and tests/test_frontend_build.py fails when it is out of date.
//
//   npm --prefix web ci && npm --prefix web run build     (or: run watch)
import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync, watch } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { transformSync } from 'esbuild';

const PUBLIC = join(dirname(fileURLToPath(import.meta.url)), '..', 'public');
export const SOURCES = [
  'tweaks-panel.jsx',
  'views/account.jsx',
  'views/setIcon.jsx',
  'views/dashboard.jsx',
  'views/browse.jsx',
  'views/sets.jsx',
  'views/deck.jsx',
  'views/lab.jsx',
  'views/graph.jsx',
  'views/valuation.jsx',
  'app.jsx',
];

function build() {
  const hash = createHash('sha256');
  const parts = SOURCES.map((file) => {
    const source = readFileSync(join(PUBLIC, file), 'utf8');
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

build();
if (process.argv.includes('--watch')) {
  for (const file of SOURCES) {
    watch(join(PUBLIC, file), () => { try { build(); } catch (e) { console.error(e.message); } });
  }
  console.log('watching for changes…');
}
