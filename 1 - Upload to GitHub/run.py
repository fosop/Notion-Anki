"""Start the Notion → Anki app on this computer only.

    python run.py              # opens your browser automatically
    python run.py --no-browser

The server binds to 127.0.0.1 (not reachable from other devices), usage
statistics are disabled, and the browser is opened only after the local
server answers its health check. This file is also the natural entry point
for a future PyInstaller build.
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

HOST = "127.0.0.1"
PROJECT_DIR = Path(__file__).resolve().parent


def _free_port(preferred: int = 8501) -> int:
    for port in range(preferred, preferred + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind((HOST, port))
            except OSError:
                continue
            return port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((HOST, 0))
        return probe.getsockname()[1]


def _open_browser_when_ready(url: str) -> None:
    # Never route the local health check through a system proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def wait_and_open() -> None:
        for _ in range(120):
            try:
                with opener.open(url + "_stcore/health", timeout=1) as response:
                    if response.status == 200:
                        webbrowser.open(url)
                        return
            except OSError:
                pass
            time.sleep(0.5)

    threading.Thread(target=wait_and_open, daemon=True).start()


def main() -> int:
    os.chdir(PROJECT_DIR)  # so Streamlit also picks up .streamlit/config.toml
    port = _free_port()
    url = f"http://{HOST}:{port}/"
    if "--no-browser" not in sys.argv:
        _open_browser_when_ready(url)

    from streamlit.web import cli as streamlit_cli

    sys.argv = [
        "streamlit",
        "run",
        str(PROJECT_DIR / "app.py"),
        f"--server.address={HOST}",
        f"--server.port={port}",
        "--server.headless=true",
        "--server.maxUploadSize=1024",
        "--browser.gatherUsageStats=false",
        f"--browser.serverAddress={HOST}",
    ]
    print(f"Notion → Anki is starting at {url}  (only reachable from this computer; press Ctrl+C to stop)")
    return streamlit_cli.main()


if __name__ == "__main__":
    sys.exit(main())
