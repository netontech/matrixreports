"""Single entry point for the compiled build.

Shipping one executable rather than two keeps the deployment to a single
folder and halves the compile. ``matrixreports.exe web`` starts the portal;
everything else goes to the ordinary command line, so ``check``, ``discover``,
``daily`` and the rest behave exactly as they do from source.
"""

from __future__ import annotations

import sys


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] == "web":
        from webapp.app import main as web_main
        sys.argv = [sys.argv[0], *argv[1:]]
        return web_main()

    from matrixreports.cli import main as cli_main
    return cli_main()


if __name__ == "__main__":
    sys.exit(main())
