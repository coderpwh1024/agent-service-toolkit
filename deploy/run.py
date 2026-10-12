"""Load deployment configuration before starting the service or Streamlit."""

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("component", choices=("service", "app"))
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    if not (root / ".env").is_file():
        parser.error("Missing .env; run bash deploy/install.sh and configure .env first.")
    os.chdir(root)
    load_dotenv(root / ".env", override=False)
    os.environ.setdefault("PYTHONUNBUFFERED", "1")

    if args.component == "service":
        command = [sys.executable, str(root / "src" / "run_service.py")]
    else:
        command = [sys.executable, "-m", "streamlit", "run", str(root / "src" / "streamlit_app.py")]
    os.execv(sys.executable, command)


if __name__ == "__main__":
    main()
