import fs from 'node:fs';
import zlib from 'node:zlib';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const distPath = path.resolve(__dirname, '../dist');

// Check that mockServiceWorker.js does not exist
const workerPath = path.join(distPath, 'mockServiceWorker.js');
if (fs.existsSync(workerPath)) {
  console.error('❌ FATAL: mockServiceWorker.js found in production build output at ' + workerPath);
  process.exit(1);
}

// Check that no JS files contain MSW or mock markers
const assetsPath = path.join(distPath, 'assets');
if (fs.existsSync(assetsPath)) {
  const files = fs.readdirSync(assetsPath).filter(f => f.endsWith('.js'));
  let foundViolations = false;

  for (const file of files) {
    const filePath = path.join(assetsPath, file);
    const content = fs.readFileSync(filePath, 'utf8');

    // Look for suspicious strings
    if (content.includes('FIXTURE MODE — NON-DURABLE')) {
      console.error(`❌ FATAL: Fixture mode banner text found in production bundle: ${file}`);
      foundViolations = true;
    }

    // Ensure setupWorker isn't bundled
    if (content.includes('setupWorker') && content.includes('msw')) {
      console.error(`❌ FATAL: MSW setup code found in production bundle: ${file}`);
      foundViolations = true;
    }

    if (content.includes('src/dev/adapters')) {
      console.error(`❌ FATAL: Dev fixtures code found in production bundle: ${file}`);
      foundViolations = true;
    }
  }

  if (foundViolations) {
    process.exit(1);
  }
}

// --- Size budget -------------------------------------------------------------
//
// This script ran on every build and only ever looked for mock artifacts, so
// nothing was watching weight. `zod` sat in the eagerly loaded entry chunk to
// validate four environment variables -- three of them inlined by Vite as
// literal constants -- and cost 57 kB raw, 13 kB gzipped, on every first paint.
// It was found by an audit rather than by a build, which is the wrong way round.
//
// Budgets are on **gzip**, because that is what a visitor downloads, and they
// carry roughly 12% headroom over the measured build: tight enough to notice a
// library arriving, loose enough that ordinary feature work does not trip them.
// Raise them deliberately, in a commit that says what earned the weight.

const ENTRY_GZIP_BUDGET = 80 * 1024;
const TOTAL_GZIP_BUDGET = 260 * 1024;

function gzipSize(filePath) {
  return zlib.gzipSync(fs.readFileSync(filePath)).length;
}

function kb(bytes) {
  return `${(bytes / 1024).toFixed(1)} kB`;
}

if (fs.existsSync(assetsPath)) {
  const scripts = fs.readdirSync(assetsPath).filter((f) => f.endsWith('.js'));
  let overBudget = false;

  // The entry chunk is the one every visitor pays for before anything renders.
  const entry = scripts.find((f) => /^index-.*\.js$/.test(f));
  if (!entry) {
    console.error('❌ FATAL: no entry chunk (index-*.js) found; the budget cannot be checked.');
    process.exit(1);
  }

  const entryGzip = gzipSize(path.join(assetsPath, entry));
  if (entryGzip > ENTRY_GZIP_BUDGET) {
    console.error(
      `❌ FATAL: entry chunk ${entry} is ${kb(entryGzip)} gzipped, over the ` +
        `${kb(ENTRY_GZIP_BUDGET)} budget. Something now loads eagerly that did not before — ` +
        'split it behind a route, load it on demand, or raise the budget on purpose.',
    );
    overBudget = true;
  }

  const totalGzip = scripts.reduce((sum, f) => sum + gzipSize(path.join(assetsPath, f)), 0);
  if (totalGzip > TOTAL_GZIP_BUDGET) {
    console.error(
      `❌ FATAL: all JavaScript totals ${kb(totalGzip)} gzipped, over the ` +
        `${kb(TOTAL_GZIP_BUDGET)} budget.`,
    );
    overBudget = true;
  }

  if (overBudget) {
    process.exit(1);
  }

  console.log(
    `✅ Size budget passed: entry ${kb(entryGzip)} / ${kb(ENTRY_GZIP_BUDGET)}, ` +
      `all JS ${kb(totalGzip)} / ${kb(TOTAL_GZIP_BUDGET)} (gzipped).`,
  );
}

console.log('✅ Bundle check passed: No mock artifacts found in production bundle.');
