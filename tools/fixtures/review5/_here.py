"""Where the review5 scripts find the adapter: the site's engine/ and the engine beside the site (relative paths)."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.normpath(os.path.join(HERE, "..", "..", ".."))
os.environ.setdefault("NL_BROWSER_STRICT", "1")
sys.path.insert(0, os.path.normpath(os.path.join(SITE, "..", "northledger-core")))
sys.path.insert(0, os.path.join(SITE, "engine"))
sys.path.insert(0, HERE)
