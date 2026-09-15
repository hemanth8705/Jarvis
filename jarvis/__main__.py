"""Run the Jarvis MCP server: python -m jarvis"""

import asyncio

from jarvis.server import main

if __name__ == '__main__':
	asyncio.run(main())
