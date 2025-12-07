"""TwinSync Spot - Main FastAPI Application."""
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware

from app.db.sqlite import Database
from app.api.routes import router as api_router
from app.api.auth import router as auth_router
from app.core.config import ConfigManager
from app.core.scheduler import get_scheduler
from app.core.analyzer import SpotAnalyzer
from app.camera.ha_adapter import HACamera
from app.core.logging_config import setup_logging
from app.version import VERSION


# Setup verbose logging
logger = setup_logging()

# Get configuration from environment
DATA_DIR = os.environ.get("DATA_DIR", "/data")

# Paths
APP_DIR = Path(__file__).parent
STATIC_DIR = APP_DIR / "web" / "static"
TEMPLATES_DIR = APP_DIR / "web" / "templates"

# Database instance
db: Database = None


class IngressMiddleware(BaseHTTPMiddleware):
    """Middleware to handle Home Assistant ingress path rewriting.
    
    Home Assistant sends requests with X-Ingress-Path header containing the
    ingress prefix (e.g., /api/hassio_ingress/xxx). This middleware:
    1. Stores the ingress path in request.state for templates
    2. Strips the ingress path from incoming request paths
    
    This allows routes to be registered at standard paths (/, /add, /api/spots)
    while correctly handling HA ingress requests.
    """
    
    async def dispatch(self, request: Request, call_next):
        # Get ingress path from header and validate format
        ingress_path = request.headers.get("X-Ingress-Path", "").rstrip("/")
        
        # Validate ingress path format - must start with / if present
        if ingress_path and not ingress_path.startswith("/"):
            ingress_path = ""
            logger.warning("Invalid X-Ingress-Path header (must start with /), ignoring")
        
        request.state.ingress_path = ingress_path
        
        # Strip ingress path from request path if present
        path = request.scope.get("path", "/")
        if ingress_path and path.startswith(ingress_path):
            new_path = path[len(ingress_path):] or "/"
            request.scope["path"] = new_path
            logger.debug(f"Ingress: Rewrote path {path} -> {new_path}")
        
        return await call_next(request)


class LoggingMiddleware(BaseHTTPMiddleware):
    """Middleware to log all requests with timing information."""
    
    async def dispatch(self, request: Request, call_next):
        logger.info(f"→ {request.method} {request.url.path}")
        logger.debug(f"  Headers: X-Ingress-Path={request.headers.get('X-Ingress-Path', 'none')}")
        
        start = time.time()
        response = await call_next(request)
        elapsed = (time.time() - start) * 1000
        
        logger.info(f"← {request.method} {request.url.path} | {response.status_code} | {elapsed:.1f}ms")
        return response


def get_ingress_path(request: Request) -> str:
    """Get the ingress path from request state (set by middleware)."""
    return getattr(request.state, "ingress_path", "")


async def run_scheduled_check(spot_id: int):
    """Callback for scheduled spot checks."""
    global db
    
    logger.info(f"Scheduler: Running check for spot {spot_id}")
    
    if not db:
        logger.error("Scheduler: Database not initialized for scheduled check")
        return
    
    db_path = str(Path(DATA_DIR) / "twinsync.db")
    
    try:
        spot = await db.get_spot(spot_id)
        if not spot:
            logger.warning(f"Scheduler: Spot {spot_id} not found for scheduled check")
            return
        
        # Get camera snapshot
        logger.info(f"Camera: Getting snapshot from {spot.camera_entity}")
        camera = HACamera(db_path)
        image_bytes = await camera.get_snapshot(spot.camera_entity)
        
        if not image_bytes:
            logger.error(f"Camera: Failed to get snapshot for spot {spot_id}")
            return
        
        logger.debug(f"Camera: Got snapshot ({len(image_bytes)} bytes)")
        
        # Get memory for context
        memory = await db.get_spot_memory(spot_id)
        
        # Analyze with Gemini
        logger.debug(f"Gemini API: Analyzing image ({len(image_bytes)} bytes)")
        analyzer = SpotAnalyzer(db_path)
        result = await analyzer.analyze(
            image_bytes=image_bytes,
            spot_name=spot.name,
            definition=spot.definition,
            voice=spot.voice,
            custom_voice_prompt=spot.custom_voice_prompt,
            memory=memory,
        )
        
        # Save check result
        logger.debug(f"DB: Saving check result for spot {spot_id}")
        await db.save_check(spot_id, result)
        
        # If needs_attention, reset streak
        if result.status == "needs_attention":
            await db.update_spot(spot_id, current_streak=0)
        
        logger.info(f"Scheduler: Check completed for spot {spot_id}: {result.status}")
        
    except Exception as e:
        logger.error(f"Scheduler: Error in check for spot {spot_id}: {e}", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan - startup and shutdown."""
    global db
    
    logger.info(f"TwinSync Spot v{VERSION} starting...")
    
    # Startup
    db_path = Path(DATA_DIR) / "twinsync.db"
    logger.debug(f"DB: Initializing database at {db_path}")
    db = Database(str(db_path))
    await db.init()
    
    # Store db in app state for access in routes
    app.state.db = db
    
    # Start the scheduler
    logger.debug("Scheduler: Starting scheduler")
    scheduler = get_scheduler()
    await scheduler.start(db, check_callback=run_scheduled_check)
    
    logger.info(f"TwinSync Spot v{VERSION} started successfully")
    
    yield
    
    # Shutdown
    logger.info("TwinSync Spot shutting down...")
    await scheduler.stop()
    await db.close()
    logger.info("TwinSync Spot stopped")


# Create FastAPI app
app = FastAPI(
    title="TwinSync Spot",
    description="Does this match YOUR definition?",
    version=VERSION,
    lifespan=lifespan,
    docs_url="/docs",
    openapi_url="/openapi.json",
)

# Add middleware BEFORE route registration (order matters - last added runs first)
# LoggingMiddleware runs first (outer), then IngressMiddleware (inner)
app.add_middleware(LoggingMiddleware)
app.add_middleware(IngressMiddleware)

# Mount static files (at standard paths - middleware handles ingress rewriting)
app.mount(
    "/static",
    StaticFiles(directory=str(STATIC_DIR)),
    name="static"
)

# Templates
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# Include API routes (at standard paths - middleware handles ingress rewriting)
app.include_router(api_router, prefix="/api")
app.include_router(auth_router, prefix="/api")


# 404 handler with useful debug info
@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    """Custom 404 handler with useful debug information."""
    logger.error(f"404: {request.method} {request.url.path}")
    logger.error(f"  Original path: {request.scope.get('path')}")
    logger.error(f"  X-Ingress-Path: {request.headers.get('X-Ingress-Path', 'none')}")
    
    return JSONResponse(
        status_code=404,
        content={
            "detail": "Not Found",
            "path": str(request.url.path),
            "method": request.method,
            "hint": "Check logs for ingress path issues"
        }
    )


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Main page."""
    ingress_path = get_ingress_path(request)

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "ingress_path": ingress_path,
        }
    )


@app.get("/add", response_class=HTMLResponse)
async def add_spot_page(request: Request):
    """Add spot page."""
    ingress_path = get_ingress_path(request)

    return templates.TemplateResponse(
        "add_spot.html",
        {
            "request": request,
            "ingress_path": ingress_path,
        }
    )


@app.get("/spot/{spot_id}", response_class=HTMLResponse)
async def spot_detail_page(request: Request, spot_id: int):
    """Spot detail page."""
    ingress_path = get_ingress_path(request)

    return templates.TemplateResponse(
        "spot_detail.html",
        {
            "request": request,
            "spot_id": spot_id,
            "ingress_path": ingress_path,
        }
    )


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    """Settings page."""
    ingress_path = get_ingress_path(request)

    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "ingress_path": ingress_path,
            "mode": "addon" if os.environ.get("SUPERVISOR_TOKEN") else "standalone",
        }
    )


@app.get("/wizard", response_class=HTMLResponse)
async def wizard_page(request: Request):
    """Setup wizard page for first-time users."""
    ingress_path = get_ingress_path(request)

    return templates.TemplateResponse(
        "wizard.html",
        {
            "request": request,
            "ingress_path": ingress_path,
            "mode": "addon" if os.environ.get("SUPERVISOR_TOKEN") else "standalone",
        }
    )
