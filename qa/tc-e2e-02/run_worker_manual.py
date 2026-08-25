"""Run a platform worker with AI routing pinned to the MANUAL provider.

TC-E2E-02 Phase A: same worker, same code path, but every model call goes to
`ManualFileProvider` (answered by qa/tc-e2e-02/responder.py) instead of a
hosted provider. Requires the active release to declare no AI providers, so
routing falls back to the process environment set here.

Usage: run_worker_manual.py <path-to-worker-script>
"""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

os.environ["PLATFORM_AI_PROVIDER_ORDER"] = "MANUAL"
# AUTO hands MANUAL to the durable interception store when the process has one,
# which parks requests for a human in the console. Phase A answers over the
# filesystem, so ask for the file handoff explicitly.
os.environ["PLATFORM_AI_MANUAL_HANDOFF"] = "FILE"
# Deterministic runs must not wait out a 280s hosted-provider budget when the
# scripted responder answers in under a second.
os.environ.setdefault("PLATFORM_AI_TIMEOUT_SECONDS", "60")
os.environ.setdefault("PLATFORM_AI_GLOBAL_TIMEOUT_SECONDS", "120")

if len(sys.argv) < 2:
    raise SystemExit("usage: run_worker_manual.py <worker-script>")

# The provider resolves `.manual_llm` against the process CWD and offers no
# setting for it; the harness cannot rely on the CWD the launcher picks, so the
# module default is pinned to one absolute path both sides agree on.
import return_platform.ai.providers.manual as _manual  # noqa: E402

_manual.DEFAULT_MANUAL_LLM_DIR = Path(
    os.environ.get(
        "TCE2E02_MANUAL_DIR",
        str(Path(__file__).resolve().parents[2] / ".manual_llm"),
    )
)
print("manual provider dir:", _manual.DEFAULT_MANUAL_LLM_DIR, flush=True)

target = sys.argv[1]
sys.argv = sys.argv[1:]
runpy.run_path(target, run_name="__main__")
