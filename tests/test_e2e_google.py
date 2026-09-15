"""Second-site end-to-end run: proves the brain and skill matching are not
tuned to one domain. Same flow as the YouTube test against google.com.

    PYTHONPATH=. JARVIS_HEADLESS=true .venv/Scripts/python.exe tests/test_e2e_google.py
"""

import asyncio
import json
import os
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from jarvis.server import JarvisServer

RESULTS = []


def check(label, cond, detail=''):
    RESULTS.append((label, cond))
    print(('PASS  ' if cond else 'FAIL  ') + label + (f'  :: {detail}' if detail and not cond else ''))
    sys.stdout.flush()


async def call(srv, _tool, /, **args):
    out = await srv._dispatch(_tool, args)
    if isinstance(out, list):
        return next((c.text for c in out if getattr(c, 'type', '') == 'text'), '[image]')
    return out


def as_json(s):
    try:
        return json.loads(s)
    except Exception:
        return {}


def find_index(page, **criteria):
    for el in page.get('interactive_elements', []):
        if all(str(criteria[k]).lower() in str(el.get(k, '')).lower() for k in criteria):
            return el['index']
    return None


async def main():
    srv = JarvisServer(headless=os.getenv('JARVIS_HEADLESS', 'true').lower() == 'true')
    print(f'run_id={srv.run_id}\n')

    try:
        await call(srv, 'plan_create',
                   goal='Search Google for "browser use github" and read the result titles',
                   steps=['Open google.com', 'Enter the query and submit', 'Read the result titles'])

        nxt = as_json(await call(srv, 'plan_next'))
        check('step 1 active', nxt.get('step', {}).get('n') == 1)

        await call(srv, 'browser_navigate', url='https://www.google.com')
        await call(srv, 'plan_observe', status='ok', note='google loaded')

        nxt = as_json(await call(srv, 'plan_next'))
        page = nxt.get('page', {})
        check('on google', 'google.' in page.get('url', ''), page.get('url'))
        check('google.com skill injected', (nxt.get('skill') or {}).get('domain') == 'google.com',
              nxt.get('skill_hint'))

        idx = find_index(page, name='q')
        if idx is None:
            idx = find_index(page, tag='textarea')
        if idx is None:
            idx = find_index(page, **{'aria-label': 'Search'})
        check('search field located', idx is not None,
              page.get('interactive_elements', [])[:6])

        if idx is not None:
            await call(srv, 'browser_type', index=idx, text='browser use github')
            await call(srv, 'browser_send_keys', keys='Enter')
            await call(srv, 'plan_observe', status='ok', note=f'submitted from index {idx}')
            await asyncio.sleep(3)

            nxt = as_json(await call(srv, 'plan_next'))
            url = nxt.get('page', {}).get('url', '')
            check('reached results page', 'search' in url or 'q=' in url, url)

            md = await call(srv, 'browser_markdown')
            check('markdown extracted with no LLM', len(md) > 200, f'len={len(md)}')
            check('markdown mentions the query', 'browser' in md.lower(), md[:200])
            print('\n--- markdown head ---')
            print(md[:400].replace('\n\n', '\n'))
            print('---\n')

            await call(srv, 'plan_observe', status='ok', note='titles read')

        fin = as_json(await call(srv, 'plan_next'))
        check('plan complete', fin.get('complete') is True, fin)

    finally:
        await srv.session.close()

    print()
    failed = [l for l, c in RESULTS if not c]
    print(f'{len(RESULTS) - len(failed)}/{len(RESULTS)} passed')
    if failed:
        print('FAILED:', failed)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
