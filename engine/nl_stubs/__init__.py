"""Stand-ins for standard modules that a browser build of Python may lack.

nl_browser installs one only when the real module cannot be imported, so the same
code runs unchanged in CPython, where every real module exists.
"""
