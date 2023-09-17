from __future__ import annotations

import logging
import multiprocessing
import os
import sys

logging.basicConfig(level=logging.WARNING)


def main():
    multiprocessing.freeze_support()

    import randovania

    # Add our local dotnet to path
    dotnet_path = randovania.get_data_path().joinpath("dotnet_runtime")
    os.environ["PATH"] = f'{dotnet_path}{os.pathsep}{os.environ["PATH"]}'

    randovania.setup_logging("INFO", None, quiet=True)

    logging.debug("Starting Randovania...")

    from randovania import cli

    cli.run_cli(sys.argv)


if __name__ == "__main__":
    main()
