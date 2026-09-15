"""End-to-end: drive the real Jarvis tool surface through a YouTube search.

Calls JarvisServer._dispatch directly - the same code path the MCP host hits,
minus the stdio transport. Scripted (not LLM-driven) so it is deterministic and
proves the tool layer, the brain and the skill injection all actually work.
"""

import asyncio
import json
import os
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

os.environ['JARVIS_HEADLESS'] = os.getenv('JARVIS_HEADLESS', 'false')

# Exercise skill_learn against a scratch copy so the repo's skills stay pristine.
import shutil
import tempfile
from pathlib import Path

_scratch = Path(tempfile.mkdtemp(prefix='jarvis-skills-'))
shutil.copytree(Path(__file__).parent.parent / 'jarvis' / 'skills', _scratch, dirs_exist_ok=True)
os.environ['JARVIS_SKILLS_DIR'] = str(_scratch)

from jarvis.server import JarvisServer

RESULTS = []


def check(label, cond, detail=''):
    RESULTS.append((label, cond))
    print(('PASS  ' if cond else 'FAIL  ') + label + (f'  :: {detail}' if detail and not cond else ''))
    sys.stdout.flush()


async def call(srv, _tool, /, **args):
    out = await srv._dispatch(_tool, args)
    if isinstance(out, list):
        kinds = [getattr(c, 'type', '?') for c in out]
        return {'_content_kinds': kinds, '_text': next((c.text for c in out if getattr(c, 'type', '') == 'text'), '')}
    return out


def as_json(s):
    try:
        return json.loads(s)
    except Exception:
        return {}


def find_index(page, **criteria):
    """First interactive element matching all criteria (substring match)."""
    for el in page.get('interactive_elements', []):
        if all(str(criteria[k]).lower() in str(el.get(k, '')).lower() for k in criteria):
            return el['index']
    return None


async def main():
    srv = JarvisServer(headless=os.environ['JARVIS_HEADLESS'].lower() == 'true')
    print(f'run_id={srv.run_id}  journal={srv.journal.path}\n')

    try:
        # 1. Plan
        out = as_json(await call(srv, 'plan_create',
            goal='Search YouTube for "lofi hip hop" and open the first video',
            steps=[
                'Navigate to youtube.com',
                'Type the query into the search box',
                'Press Enter to run the search',
                'Confirm result titles are present',
            ]))
        check('plan_create returns 4 steps', out.get('steps') == 4, out)

        # 2. Step 1 - navigate
        nxt = as_json(await call(srv, 'plan_next'))
        check('plan_next returns step 1', nxt.get('step', {}).get('n') == 1, nxt.get('step'))
        check('plan_next carries goal + page', 'page' in nxt and 'goal' in nxt)

        await call(srv, 'browser_navigate', url='https://www.youtube.com')
        await call(srv, 'plan_observe', status='ok', note='youtube loaded')

        # 3. Step 2 - skill injection must now fire on the youtube.com domain
        nxt = as_json(await call(srv, 'plan_next'))
        page = nxt.get('page', {})
        check('now on youtube', 'youtube.com' in page.get('url', ''), page.get('url'))
        check('skill injected for youtube.com', (nxt.get('skill') or {}).get('domain') == 'youtube.com',
              nxt.get('skill_hint'))
        check('page exposes interactive elements', len(page.get('interactive_elements', [])) > 0)

        # 4. Find + fill the search box
        idx = find_index(page, id='search', tag='input')
        if idx is None:
            idx = find_index(page, placeholder='Search')
        if idx is None:
            idx = find_index(page, **{'aria-label': 'Search'})
        check('search box located in state', idx is not None,
              [e for e in page.get('interactive_elements', [])][:8])

        if idx is not None:
            r = await call(srv, 'browser_type', index=idx, text='lofi hip hop')
            check('browser_type succeeded', 'error' not in str(r).lower(), r)
            await call(srv, 'plan_observe', status='ok', note=f'typed into index {idx}')

            # 5. THE tool the upstream MCP does not expose at all
            nxt = as_json(await call(srv, 'plan_next'))
            check('plan_next advanced to step 3', nxt.get('step', {}).get('n') == 3)
            r = await call(srv, 'browser_send_keys', keys='Enter')
            check('browser_send_keys Enter succeeded', 'error' not in str(r).lower(), r)
            await call(srv, 'plan_observe', status='ok', note='pressed Enter')

            await asyncio.sleep(3)

            # 6. Verify results with a zero-LLM read
            nxt = as_json(await call(srv, 'plan_next'))
            page = nxt.get('page', {})
            check('navigated to results page', 'results' in page.get('url', '') or 'search_query' in page.get('url', ''),
                  page.get('url'))

            titles = await call(srv, 'browser_find_elements',
                                selector='a#video-title', attributes=['title'], max_results=5, include_text=True)
            check('found result titles without an LLM', 'video-title' in str(titles) or 'lofi' in str(titles).lower(),
                  str(titles)[:300])
            print('\n--- first results ---')
            print(str(titles)[:600])
            print('---\n')

            # 7. browser_evaluate - also absent upstream
            ev = await call(srv, 'browser_evaluate',
                            code='(function(){return {n:document.querySelectorAll("a#video-title").length, u:location.href};})()')
            check('browser_evaluate returned data', 'n' in str(ev), str(ev)[:200])

            await call(srv, 'plan_observe', status='ok', note='results verified')

        # 8. Plan should now be complete
        fin = as_json(await call(srv, 'plan_next'))
        check('plan reports complete', fin.get('complete') is True, fin)

        # 9. Journal captured the run
        entries = srv.journal.read()
        kinds = {e['kind'] for e in entries}
        check('journal recorded plan + actions', {'plan_create', 'action', 'step_observe'} <= kinds, kinds)
        print(f'journal entries: {len(entries)}  kinds={sorted(kinds)}')

        # 10. Skill learning writes back
        await call(srv, 'skill_learn', domain='youtube.com', lesson='e2e smoke: Enter on the search box works reliably.')
        body = await call(srv, 'skill_get', name='youtube.com')
        check('skill_learn persisted', 'e2e smoke' in body)

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
