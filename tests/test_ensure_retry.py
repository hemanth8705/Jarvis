"""Regression test for the Session.ensure() retry bug: a failed browser start
must leave self.browser as None so the next call retries with a fresh
BrowserSession, instead of permanently handing back a dead half-started one."""

import asyncio
from unittest.mock import AsyncMock, patch

from jarvis.session import Session


async def main():
    session = Session(headless=True)

    call_count = {'n': 0}
    real_start = None

    async def flaky_start(self):
        call_count['n'] += 1
        if call_count['n'] == 1:
            raise RuntimeError('simulated: Chrome process exited before CDP became available')
        # second call: pretend it succeeded, no real browser needed for this check
        return None

    with patch('browser_use.browser.session.BrowserSession.start', new=flaky_start), \
         patch('browser_use.browser.session.BrowserSession.kill', new=AsyncMock()):

        # First call: start() raises. self.browser must NOT be left set.
        try:
            await session.ensure()
            print('FAIL: first ensure() should have raised')
            return 1
        except RuntimeError as e:
            print('PASS: first ensure() raised as expected:', e)

        if session.browser is not None:
            print('FAIL: session.browser is not None after a failed start() -- THE BUG IS STILL PRESENT')
            return 1
        print('PASS: session.browser is None after the failed start (no stuck dead session)')

        # Second call: start() succeeds this time. Must actually retry, not
        # just return a cached dead object (there is none to return anyway
        # now, but confirm a fresh BrowserSession object is constructed).
        browser = await session.ensure()
        print('PASS: second ensure() succeeded, call_count =', call_count['n'])
        if call_count['n'] != 2:
            print('FAIL: expected exactly 2 start() attempts, got', call_count['n'])
            return 1
        if session.browser is None:
            print('FAIL: session.browser still None after a successful start()')
            return 1
        print('PASS: session.browser is set after the successful retry')

    print('\nALL PASS')
    return 0


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
