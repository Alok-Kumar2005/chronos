"""Static file serving for the UI (SPA fallback)."""

import os
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

router = APIRouter(tags=["UI"])

# Get the path to the frontend dist folder
FRONTEND_DIST = Path(__file__).parent.parent.parent.parent.parent / "frontend" / "dist"


def mount_static_files(app):
    """Mount static files and set up SPA fallback route."""
    
    # Serve static assets (JS, CSS, etc.) with far-future cache headers
    if FRONTEND_DIST.exists() and (FRONTEND_DIST / "assets").exists():
        app.mount(
            "/ui/assets",
            StaticFiles(directory=str(FRONTEND_DIST / "assets")),
            name="assets",
        )
    
    # SPA fallback: serve index.html for unknown /ui/* routes
    @app.get("/ui/{full_path:path}", tags=["UI"])
    async def serve_spa(full_path: str):
        """
        Serve SPA frontend.
        
        For known assets (js, css, images), serve them directly.
        For unknown routes, serve index.html (SPA fallback).
        """
        # If it's an asset, try to serve it
        if full_path.startswith("assets/"):
            asset_path = FRONTEND_DIST / full_path
            if asset_path.exists() and asset_path.is_file():
                return FileResponse(asset_path)
        
        # For SPA routes, serve index.html
        index_path = FRONTEND_DIST / "index.html"
        if index_path.exists():
            return FileResponse(index_path, media_type="text/html")
        
        # Fallback if dist doesn't exist
        return HTMLResponse(
            """
            <!DOCTYPE html>
            <html>
                <head>
                    <title>Chronos</title>
                    <meta charset="utf-8" />
                    <meta name="viewport" content="width=device-width, initial-scale=1" />
                </head>
                <body style="background: #0f172a; color: #e2e8f0; font-family: system-ui;">
                    <div style="display: flex; align-items: center; justify-content: center; min-height: 100vh;">
                        <div style="text-align: center;">
                            <h1>Chronos</h1>
                            <p>Frontend is building. Please wait...</p>
                            <pre style="color: #64748b; font-size: 12px;">
                                npm run build in frontend/
                            </pre>
                        </div>
                    </div>
                </body>
            </html>
            """
        )
    
    # Root redirect to /ui
    @app.get("/", include_in_schema=False)
    async def root():
        """Redirect root to /ui."""
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/ui/", status_code=307)
