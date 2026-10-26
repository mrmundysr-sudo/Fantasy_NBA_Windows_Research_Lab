"""PyInstaller entry point (absolute import; nbalab/__main__.py uses a relative one)."""
import sys
from nbalab.app import main

if __name__ == "__main__":
    sys.exit(main())
