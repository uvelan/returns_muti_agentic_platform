import fs from 'node:fs';
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

console.log('✅ Bundle check passed: No mock artifacts found in production bundle.');
