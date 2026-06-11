"""Launcher: python run.py, then open http://127.0.0.1:8770."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from readafp.app import create_app

if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=8770, debug=True)
