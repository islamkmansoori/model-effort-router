"""Run the routing tools: python3 -m routing_tools <command>."""
import sys

from .cli import main

sys.exit(main(sys.argv[1:]))
