#!/usr/bin/env python
"""Short launcher so every agops command is `py tools\\agops.py <cmd>`.

The real code is the tools/agops/ package next to this file. Python resolves a
package before a same-named module, so `from agops import core` inside the
package still finds the package and never this shim -- but nothing here imports
by name anyway; the CLI is loaded by explicit path.
"""
import os
import runpy
import sys

CLI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agops", "cli.py")

if __name__ == "__main__":
    sys.argv[0] = CLI
    runpy.run_path(CLI, run_name="__main__")
