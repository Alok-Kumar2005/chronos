#!/usr/bin/env python
"""Windows-compatible launcher for Chronos backend."""

import sys
import asyncio

# Windows event loop fix MUST happen before uvicorn starts
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Now import uvicorn
import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
