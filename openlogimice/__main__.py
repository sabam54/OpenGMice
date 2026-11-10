import sys

from .cli import main as cli_main

if len(sys.argv) > 1:
    sys.exit(cli_main(sys.argv[1:]))
from .gui import main  # noqa: E402

sys.exit(main())
