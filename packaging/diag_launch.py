"""PyInstaller entry point for NBA-Source-Diagnostics.exe"""
import sys
from nbalab.diag_app import main

if __name__ == "__main__":
    sys.exit(main())
