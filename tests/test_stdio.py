"""Smoke-test the real MCP stdio transport: spawn `python -m jarvis`, handshake,
list tools, and call a brain tool. This is exactly what Claude Code does."""

import asyncio
import os
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = r'D:\FERSONAL\Jarvis'
PY = ROOT + r'\.venv\Scripts\python.exe'


async def main():
    env = dict(os.environ)
    env['PYTHONPATH'] = ROOT
    env['JARVIS_HEADLESS'] = 'true'

    params = StdioServerParameters(command=PY, args=['-m', 'jarvis'], env=env, cwd=ROOT)

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print('server:', init.server_info.name, init.server_info.version)
            print('instructions present:', bool(init.instructions))

            tools = (await session.list_tools()).tools
            names = [t.name for t in tools]
            print('tools:', len(names))
            for must in ('plan_create', 'plan_next', 'plan_observe', 'browser_send_keys',
                         'browser_evaluate', 'browser_state', 'skill_learn'):
                print(('  OK   ' if must in names else '  MISS ') + must)

            # Drive the brain over the wire, no browser needed.
            r = await session.call_tool('plan_create', {'goal': 'stdio smoke', 'steps': ['one', 'two']})
            print('plan_create ->', r.content[0].text.replace('\n', ' ')[:120])

            r = await session.call_tool('skill_list', {})
            print('skill_list  ->', r.content[0].text.replace('\n', ' ')[:120])

            # Error path: unknown tool should come back as is_error, not a crash.
            r = await session.call_tool('plan_status', {})
            print('plan_status ->', 'is_error=' + str(r.is_error), r.content[0].text.replace('\n', ' ')[:90])

    print('\nstdio transport OK')


if __name__ == '__main__':
    asyncio.run(main())
