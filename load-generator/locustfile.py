"""
locustfile.py
=============
Default entry point so that running `locust` (without `-f`) from this
directory launches the Normal Traffic scenario. The launcher backend
always passes an explicit `-f scenarios/<name>.py`, so this file only
matters when you run Locust directly from the CLI for a quick smoke
test:

    locust --host http://localhost

Equivalent to:

    locust -f scenarios/normal.py --host http://localhost
"""

import sys
import os

# Make sibling `scenarios/` package files importable when this file is
# the one Locust loads. Locust adds the directory of the -f file to
# sys.path; when this file is at the project root, scenarios/ is a
# subdir, so we add it explicitly.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "scenarios"))

from normal import NormalUser  # noqa: E402,F401  (Locust discovers this symbol)
