"""Run the Lymbix tools: python3 -m lymbix_tools <command>."""
import sys

from .cli import main

sys.exit(main(sys.argv[1:]))
