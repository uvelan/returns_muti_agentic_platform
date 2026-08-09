"""The module's single mounted router.

One aggregation point so `module.py` can expose exactly one `APIRouter` and the
composition root mounts exactly one thing, regardless of how many sub-routers the
analyzer grows in C3.2/C3.3.
"""

from __future__ import annotations

from fastapi import APIRouter

from return_platform.graph_schema_analyzer.api.analyses import router as analyses_router

__all__ = ["router"]

router = APIRouter()
router.include_router(analyses_router)
