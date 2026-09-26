"""Launch the MARVEL web UI via `marvel-web` command."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

#: Loopback unless the deployment says otherwise (docker-compose sets
#: STREAMLIT_SERVER_ADDRESS=0.0.0.0 for the container). This is passed on the
#: command line, not only left to `.streamlit/config.toml`: Streamlit resolves
#: that file relative to the CWD and the script directory, so running the
#: documented `marvel-web` from anywhere but the repo root silently fell back to
#: Streamlit's own default of 0.0.0.0 — an unauthenticated UI on the LAN, which is
#: exactly what the config file was added to prevent.
DEFAULT_ADDRESS = "127.0.0.1"


def main() -> None:
    app_path = Path(__file__).parent / "app.py"
    address = os.environ.get("STREAMLIT_SERVER_ADDRESS") or DEFAULT_ADDRESS
    subprocess.run(
        [
            sys.executable, "-m", "streamlit", "run", str(app_path),
            f"--server.address={address}",
        ]
    )


if __name__ == "__main__":
    main()
