"""Brain state-machine checks - no browser, no LLM."""

from jarvis.brain import Brain, NoPlanError

ok = []


def check(label, cond):
    ok.append((label, cond))
    print(('PASS  ' if cond else 'FAIL  ') + label)


b = Brain()

# No plan yet
try:
    b.next_step()
    check('next_step without plan raises', False)
except NoPlanError:
    check('next_step without plan raises', True)

# Happy path
b.create('search youtube', ['open youtube', 'type query', 'press enter'])
s = b.next_step()
check('first step is n=1', s.n == 1 and s.intent == 'open youtube')
check('attempt counted', s.attempts == 1)

b.observe('ok', 'youtube loaded')
s = b.next_step()
check('advances to n=2', s.n == 2)

# Retry path
b.observe('fail', 'search box not found')
s = b.next_step()
check('failed step retries same n', s.n == 2)
check('attempt incremented', s.attempts == 2)

b.observe('fail', 'still not found')
r = b.observe('fail', 'third strike')
check('3 fails forces replan', r.get('needs_replan') is True)

# Revise
b.revise(['use search URL directly', 'verify results'], 'search box unreachable')
s = b.next_step()
check('revise renumbers from kept steps', s.intent == 'use search URL directly')
check('replan counted', b.plan.replans == 1)

b.observe('ok', 'results page loaded')
b.observe('ok', 'results verified')
s = b.next_step()
check('plan completes', s is None and b.plan.status == 'complete')

snap = b.snapshot()
check('progress reported', snap['progress'].endswith('/4'))
check('observations retained', any(st['observations'] for st in snap['steps']))

# Skip path
b2 = Brain()
b2.create('g', ['a', 'b'])
b2.next_step()
b2.observe('skip', 'not needed')
check('skip advances', b2.next_step().intent == 'b')

print()
failed = [label for label, c in ok if not c]
print(f'{len(ok) - len(failed)}/{len(ok)} passed')
if failed:
    print('FAILED:', failed)
    raise SystemExit(1)
