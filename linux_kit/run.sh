#!/bin/sh
set -eo pipefail

echo "=== Linux Automation Kit ==="
TARBALL="returns_platform.tar.gz"
EXPECTED_HASH="$1"

if [ -z "$EXPECTED_HASH" ]; then
    echo "Usage: $0 <expected_sha256_hash>"
    exit 1
fi

echo "Verifying cryptographic hash..."
ACTUAL_HASH=$(sha256sum "$TARBALL" | awk '{print $1}')
if [ "$ACTUAL_HASH" != "$EXPECTED_HASH" ]; then
    echo "ERROR: Hash mismatch!"
    echo "Expected: $EXPECTED_HASH"
    echo "Actual:   $ACTUAL_HASH"
    exit 1
fi
echo "Hash verified: $ACTUAL_HASH"

echo "Extracting codebase..."
DIR="/tmp/returns_validation"
rm -rf "$DIR"
mkdir -p "$DIR"
tar -xzf "$TARBALL" -C "$DIR"
cd "$DIR"

echo "Setting up environment..."
cp .env.example .env
chmod 400 scripts/sandbox_keyfile 2>/dev/null || true

echo "Starting application on Linux..."
docker compose down -v 2>/dev/null || true
docker compose up -d --build --wait

echo "Waiting for services to settle..."
sleep 15

echo "Running real return-flow validation via Playwright in Linux container..."
docker run --rm --network returns_validation_default \
    -w /app -v "$(pwd)/frontend:/app" \
    -e E2E_BASE_URL="http://frontend:8080" \
    mcr.microsoft.com/playwright:v1.44.1-jammy \
    bash -c "npm ci && npx playwright test --config=playwright.real.config.ts" > playwright_results.txt 2>&1 || {
        echo "Validation failed. Check playwright_results.txt"
    }

echo "Running backend quality validation on Linux..."
docker compose exec -T backend bash -c "python -m poetry run pytest -v" > backend_results.txt 2>&1 || {
        echo "Backend tests failed. Check backend_results.txt"
    }

echo "Capturing docker compose logs..."
docker compose logs > docker_compose_logs.txt 2>&1

echo "Packaging Linux evidence..."
zip -r /workspace/linux_evidence.zip playwright_results.txt backend_results.txt docker_compose_logs.txt

echo "Cleaning up..."
docker compose down -v
echo "Linux validation complete. Evidence saved to /workspace/linux_evidence.zip"
