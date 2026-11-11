"""PyInstaller entry script — wraps the package entry point."""
import sys

from adbperfetto.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
