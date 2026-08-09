"""Interactive terminal companion for the MANUAL AI provider.

Run this alongside the real server (from the same working directory, so it
watches the same .manual_llm/ folder the server writes to) while
PLATFORM_AI_PROVIDER_ORDER=MANUAL. Every time an order-agent turn from the
real UI would normally call Gemini/NVIDIA, it instead pauses and waits here.
This script shows you exactly what the model would have received, lets you
type or paste the JSON response, and the browser updates live - no external
API call, no quota.

Usage (from the backend/ directory, in a second terminal):
    poetry run python scripts/manual_llm_responder.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from return_platform.ai.providers.manual import DEFAULT_MANUAL_LLM_DIR

POLL_SECONDS = 0.5


def _print_request(payload: dict) -> None:
    print("\n" + "=" * 78)
    print(f"REQUEST {payload.get('requestId')}")
    print("=" * 78)

    user_payload = payload.get("userPayload", {})
    mode = user_payload.get("mode")
    if mode:
        print(f"mode: {mode}")

    context_json = user_payload.get("contextJson")
    if isinstance(context_json, str):
        try:
            parsed_context = json.loads(context_json)
        except json.JSONDecodeError:
            parsed_context = None
        if parsed_context is not None:
            print("\n--- contextJson (parsed) ---")
            print(json.dumps(parsed_context, indent=2, sort_keys=True))
        else:
            print("\n--- contextJson (raw) ---")
            print(context_json)

    validation_error = user_payload.get("validationError")
    if validation_error:
        print(f"\n--- validationError ---\n{validation_error}")

    print("\n--- systemPrompt (includes the required response schema) ---")
    print(payload.get("systemPrompt", ""))
    print("=" * 78)


def _read_multiline_json() -> str | None:
    print(
        "\nPaste the JSON response (a single AgentAction object), "
        "then a line with just END.\nType SKIP alone to leave this request pending."
    )
    lines: list[str] = []
    while True:
        try:
            line = input()
        except EOFError:
            return None
        if line.strip() == "END":
            break
        if line.strip() == "SKIP" and not lines:
            return None
        lines.append(line)
    text = "\n".join(lines).strip()
    if not text:
        return None
    try:
        json.loads(text)
    except json.JSONDecodeError as error:
        print(f"That wasn't valid JSON ({error}) - try again.")
        return _read_multiline_json()
    return text


def main() -> None:
    base_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_MANUAL_LLM_DIR
    requests_dir = base_dir / "requests"
    responses_dir = base_dir / "responses"
    requests_dir.mkdir(parents=True, exist_ok=True)
    responses_dir.mkdir(parents=True, exist_ok=True)

    print(f"Watching {requests_dir.resolve()} for pending LLM requests...")
    print("(Ctrl+C to stop)")

    seen: set[str] = set()
    try:
        while True:
            for request_path in sorted(requests_dir.glob("*.json")):
                request_id = request_path.stem
                if request_id in seen:
                    continue
                response_path = responses_dir / f"{request_id}.json"
                if response_path.exists():
                    seen.add(request_id)
                    continue
                try:
                    payload = json.loads(request_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue
                _print_request(payload)
                response_text = _read_multiline_json()
                if response_text is None:
                    print("Skipped - will ask again next poll.")
                    continue
                response_path.write_text(response_text, encoding="utf-8")
                seen.add(request_id)
                print(f"Response written for {request_id}.")
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
