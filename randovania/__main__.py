from __future__ import annotations

import logging
import multiprocessing
import os
import sys
from pathlib import Path

logging.basicConfig(level=logging.WARNING)


def main():
    multiprocessing.freeze_support()

    import randovania

    randovania.setup_logging("INFO", None, quiet=True)

    logging.debug("Starting Randovania...")

    from randovania import cli

    cli.run_cli(sys.argv)


if __name__ == "__main__":
    # Add our local dotnet to path
    if getattr(sys, "frozen", False):
        application_path = sys._MEIPASS
    else:
        application_path = Path(__file__).resolve().parent
    os.environ["PATH"] += f"{os.pathsep}{application_path}"
    main()
