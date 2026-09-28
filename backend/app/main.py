"""FastAPI read layer and dashboard host for Emergency AI Phase 1."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# Add project root to path for imports
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.db import DATABASE_PATH, database_counts, ensure_camera_table
from backend.app.routes.alerts import router as alerts_router
from backend.app.routes.cameras import router as cameras_router
from backend.app.routes.batch import router as batch_router
from backend.app.routes.batch_stream import router as batch_stream_router
from backend.app.routes.zones import router as zones_router
from backend.app.routes.zone_analysis import router as zone_analysis_router
from backend.app.routes.journey import router as journey_router
from database.zone_store import ensure_zone_table


FRONTEND_DIR = PROJECT_ROOT / "frontend"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # This extends the existing DB with only the cameras and camera_zones
    # tables. The incidents table remains owned by the root detector/database
    # module.
    ensure_camera_table()
    ensure_zone_table()
    yield


app = FastAPI(
    title="Emergency AI Phase 1 API",
    version="0.1.0",
    description="Read layer for detector incidents and camera source configuration.",
    lifespan=lifespan,
)

# The dashboard may be opened from file:// or another local dev server.
# Credentials are not used by this API, so wildcard origins are appropriate
# for this Phase 1 local skeleton. Production deployment should restrict this.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(alerts_router)
app.include_router(cameras_router)
app.include_router(batch_router)
app.include_router(batch_stream_router)
app.include_router(zones_router)
app.include_router(zone_analysis_router)
app.include_router(journey_router)


@app.get("/health", tags=["system"])
def health() -> dict:
    """Report the real database path and row counts."""
    return {
        "status": "ok",
        "database": str(DATABASE_PATH),
        "counts": database_counts(),
    }


if not FRONTEND_DIR.is_dir():
    raise RuntimeError(f"Frontend directory not found: {FRONTEND_DIR}")

# Registered after API routes so /alerts, /cameras, /health and /docs retain
# priority. Root and asset requests are served from the vanilla frontend.
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
