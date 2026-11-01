"""Start the offline week dashboard: python scripts/week_dashboard.py (port 8081).

No live runners or data refreshes are booted. Simulation reads existing caches only.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["STALK_NO_BOOT"] = "1"

if __name__ == "__main__":
    import uvicorn
    print("Week simulation: http://localhost:8081/#week", flush=True)
    uvicorn.run("stalk.api.server:app", host="127.0.0.1", port=8081, log_level="warning")
