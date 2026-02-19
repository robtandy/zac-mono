import asyncio
import sys

from gateway.server import run

debug = "--debug" in sys.argv
asyncio.run(run(debug=debug))
