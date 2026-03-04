# 1.1.0

import os
import uvicorn

# Entrypoint to run the FastAPI app via uvicorn
# Usage examples (PowerShell):
#   python main.py
#   $env:HOST="0.0.0.0"; $env:PORT="8000"; $env:RELOAD="1"; python main.py

if __name__ == "__main__":
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    reload = os.getenv("RELOAD", "1") in {"1", "true", "True", "yes", "on"}

    # Import string path keeps reload working correctly without importing the app here
    uvicorn.run("app.app:app", host=host, port=port, reload=reload, factory=False)
