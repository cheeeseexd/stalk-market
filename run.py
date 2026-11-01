"""Start Stalk Market at http://localhost:8080 (STALK_PORT to change, STALK_NO_BROWSER=1 to skip the tab)."""
import os
import sys
import threading
import time
import webbrowser

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import uvicorn
from dotenv import load_dotenv

load_dotenv()


def open_browser(port: int) -> None:
    time.sleep(1.5)
    url = f"http://localhost:{port}"
    print(f"\n  Stalk Market is running at {url}\n")
    if os.getenv("STALK_NO_BROWSER") != "1":
        try:
            webbrowser.open(url)
        except Exception:
            pass


if __name__ == "__main__":
    port = int(os.getenv("STALK_PORT", "8080"))
    threading.Thread(target=open_browser, args=(port,), daemon=True).start()
    uvicorn.run("stalk.api.server:app", host="127.0.0.1", port=port, log_level="warning")
