"""Worker/supervisor routing and cost-summary checks - no browser, no LLM API calls."""

import tempfile
from pathlib import Path

from jarvis.journal import Journal
from jarvis.runner import SUPERVISOR_MODEL, WORKER_MODEL, _cost_summary, _role_for_turn

ok = []


def check(label, cond):
    ok.append((label, cond))
    print(('PASS  ' if cond else 'FAIL  ') + label)


# -- role routing ---------------------------------------------------------
check('turn 0 goes to the supervisor', _role_for_turn(0, False) == ('supervisor', SUPERVISOR_MODEL))
check('a later turn with no replan goes to the worker', _role_for_turn(5, False) == ('worker', WORKER_MODEL))
check('a forced replan escalates any turn to the supervisor', _role_for_turn(7, True) == ('supervisor', SUPERVISOR_MODEL))

# -- cost summary -----------------------------------------------------------
with tempfile.TemporaryDirectory() as tmp:
    journal = Journal('test-run', base_dir=Path(tmp))
    journal.write('llm_call', turn=0, role='supervisor', model='claude-opus-5', input_tokens=1000, output_tokens=500)
    journal.write('llm_call', turn=1, role='worker', model='claude-haiku-4-5', input_tokens=2000, output_tokens=100)
    journal.write('llm_call', turn=2, role='worker', model='claude-haiku-4-5', input_tokens=2000, output_tokens=100)
    journal.write('action', tool='browser_click', args={}, result='ok')  # non-llm_call record must be ignored

    summary = _cost_summary(journal)

    expected_supervisor_cost = 1000 * 5.00 / 1_000_000 + 500 * 25.00 / 1_000_000
    expected_worker_cost = 2 * (2000 * 1.00 / 1_000_000 + 100 * 5.00 / 1_000_000)

    check('summary reports 2 worker calls', 'worker: 2 calls' in summary)
    check('summary reports 1 supervisor call', 'supervisor: 1 calls' in summary)
    check('supervisor cost matches opus pricing', f'${expected_supervisor_cost:.4f}' in summary)
    check('worker cost matches haiku pricing', f'${expected_worker_cost:.4f}' in summary)
    check('non-llm_call journal records are ignored', 'browser_click' not in summary)

print()
failed = [label for label, c in ok if not c]
print(f'{len(ok) - len(failed)}/{len(ok)} passed')
if failed:
    print('FAILED:', failed)
    raise SystemExit(1)
