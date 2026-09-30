"""Run a review5 script's main() and write what it prints to <script>.out beside it."""
import io
import os
import sys
from contextlib import redirect_stdout


def run(main, path):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main()
    sys.stdout.write(buf.getvalue())
    with open(os.path.splitext(path)[0] + ".out", "w", encoding="utf-8") as fh:
        fh.write(buf.getvalue())
    sys.exit(rc or 0)
