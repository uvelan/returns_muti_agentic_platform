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

// --- Size ratchet ------------------------------------------------------------
//
// This script ran on every build and only ever looked for mock artifacts, so
// nothing was watching weight. `zod` sat in the eagerly loaded entry chunk to
// validate four environment variables -- three of them inlined by Vite as
// literal constants -- and cost 57 kB raw, 13 kB gzipped, on every first paint.
// It was found by an audit rather than by a build, which is the wrong way round.
//
// It is measured on **gzip**, because that is what a visitor downloads.
//
// ## Why this is a ratchet and no longer two fixed numbers
//
// The fixed budgets that stood here (80 kB entry, 260 kB total, "roughly 12%
// headroom") answered the next real question wrong in both directions at once.
// The total came in 18.5 kB over and read as an emergency, when a module-to-
// chunk report showed zero duplicated modules and zero double-resolved
// packages -- the weight was real, lazily split, and mostly one route's graph
// library. The entry passed, but with single-digit percent left, which is a
// gate that fires on the next ordinary commit for no reason anybody can act on.
//
// Both failures are the same failure: somebody picked a number once, and every
// later change had to be argued against that number instead of against itself.
// A ratchet has no number to pick. It records what the build measured and fails
// on GROWTH, so the question a diff has to answer is "what did you add, and was
// it worth it" -- which is what the old comment asked for in prose and had no
// way to enforce.
//
// The recorded values live in `bundle-budget.json` rather than in this file, so
// changing them is a visible diff in a data file a reviewer will look at,
// instead of an edited constant inside a script nobody reads twice.

const budgetPath = path.resolve(__dirname, '../bundle-budget.json');

/**
 * How much drift is not growth.
 *
 * gzip output is not byte-identical across zlib versions, and CI runs a
 * different Node patch release than most laptops. Without a small allowance the
 * ratchet would flake on the toolchain rather than on the bundle. 0.5% is about
 * an order of magnitude more than observed zlib drift and about an order of
 * magnitude less than any change worth noticing -- 1.4 kB on today's total.
 */
const GROWTH_ALLOWANCE = 0.005;

/**
 * How far below the record counts as a real reduction that should be banked.
 *
 * Same reasoning as `scripts/ci/known_test_failures.json` being self-pruning: a
 * baseline that can only ever go up rots into a blanket excuse, and the slack
 * silently becomes the next person's headroom for weight they never justified.
 * Looser than the growth allowance on purpose -- this should fire on somebody
 * deleting a dependency, not on compression noise.
 */
const SHRINK_ALLOWANCE = 0.03;

function gzipSize(filePath) {
  return zlib.gzipSync(fs.readFileSync(filePath)).length;
}

function kb(bytes) {
  return `${(bytes / 1024).toFixed(1)} kB`;
}

if (fs.existsSync(assetsPath)) {
  if (!fs.existsSync(budgetPath)) {
    console.error(
      `❌ FATAL: ${budgetPath} is missing, so there is nothing to ratchet against. ` +
        'A size check with no recorded baseline is not a check.',
    );
    process.exit(1);
  }

  const recorded = JSON.parse(fs.readFileSync(budgetPath, 'utf8'));
  for (const key of ['entryGzipBytes', 'totalGzipBytes']) {
    // Not a truthiness test: `0` is falsy and would read as "absent", and a
    // baseline of 0 must fail loudly rather than be treated as unset.
    if (typeof recorded[key] !== 'number' || !Number.isFinite(recorded[key]) || recorded[key] <= 0) {
      console.error(`❌ FATAL: bundle-budget.json has no usable ${key}.`);
      process.exit(1);
    }
  }

  const scripts = fs.readdirSync(assetsPath).filter((f) => f.endsWith('.js'));

  // The entry chunk is the one every visitor pays for before anything renders.
  const entry = scripts.find((f) => /^index-.*\.js$/.test(f));
  if (!entry) {
    console.error('❌ FATAL: no entry chunk (index-*.js) found; the size cannot be checked.');
    process.exit(1);
  }

  const measurements = [
    { label: `entry chunk (${entry})`, key: 'entryGzipBytes', measured: gzipSize(path.join(assetsPath, entry)) },
    {
      label: 'all JavaScript',
      key: 'totalGzipBytes',
      measured: scripts.reduce((sum, f) => sum + gzipSize(path.join(assetsPath, f)), 0),
    },
  ];

  let failed = false;

  for (const { label, key, measured } of measurements) {
    const baseline = recorded[key];
    const delta = measured - baseline;
    const drift = delta / baseline;

    if (drift > GROWTH_ALLOWANCE) {
      console.error(
        `❌ FATAL: ${label} grew to ${kb(measured)} gzipped, up ${kb(delta)} ` +
          `(${(drift * 100).toFixed(1)}%) from the recorded ${kb(baseline)}.\n` +
          '   Something in this change added weight. Split it behind a route, load it on\n' +
          '   demand, or record the new size in frontend/bundle-budget.json in a commit\n' +
          `   that says what earned it:  "${key}": ${measured}`,
      );
      failed = true;
    } else if (-drift > SHRINK_ALLOWANCE) {
      console.error(
        `❌ FATAL: ${label} is ${kb(-delta)} SMALLER than the recorded ${kb(baseline)} ` +
          `(now ${kb(measured)}).\n` +
          '   This is not a complaint about the build -- it is a real reduction, and the\n' +
          '   ratchet has to be tightened to keep it. Leaving the old number would hand\n' +
          '   the next change free headroom nobody justified. Record it:\n' +
          `     "${key}": ${measured}`,
      );
      failed = true;
    }
  }

  if (failed) {
    process.exit(1);
  }

  console.log(
    `✅ Size ratchet held: entry ${kb(measurements[0].measured)} ` +
      `(recorded ${kb(recorded.entryGzipBytes)}), all JS ${kb(measurements[1].measured)} ` +
      `(recorded ${kb(recorded.totalGzipBytes)}), gzipped.`,
  );
}

console.log('✅ Bundle check passed: No mock artifacts found in production bundle.');
