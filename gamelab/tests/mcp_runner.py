"""Test-only default Host relocation for an isolated client of a live server."""
import os
import runpy

import gamelab.hosts

gamelab.hosts.HOST_PORT = int(os.environ["GAMELAB_TEST_HOST_PORT"])
runpy.run_module("gamelab.mcp", run_name="__main__")
