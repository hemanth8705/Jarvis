"""The brain: a plan state machine.

It does not think. It *structures* thinking and carries memory between tool
calls, so the host LLM has to commit to a plan, observe each step's real
outcome, and replan explicitly instead of drifting.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

StepStatus = Literal['pending', 'active', 'done', 'failed', 'skipped']


@dataclass
class Step:
	n: int
	intent: str
	status: StepStatus = 'pending'
	attempts: int = 0
	failures: int = 0
	observations: list[str] = field(default_factory=list)


@dataclass
class Plan:
	plan_id: str
	goal: str
	steps: list[Step]
	created_at: float
	status: Literal['active', 'complete', 'abandoned'] = 'active'
	replans: int = 0

	def current(self) -> Step | None:
		for s in self.steps:
			if s.status in ('pending', 'active'):
				return s
		return None

	def progress(self) -> str:
		done = sum(1 for s in self.steps if s.status == 'done')
		return f'{done}/{len(self.steps)}'


class NoPlanError(RuntimeError):
	pass


class Brain:
	"""Holds the active plan. One plan at a time, by design."""

	MAX_ATTEMPTS_PER_STEP = 3

	def __init__(self, journal=None):
		self.plan: Plan | None = None
		self.journal = journal

	# -- lifecycle ----------------------------------------------------------
	def create(self, goal: str, steps: list[str]) -> Plan:
		if not steps:
			raise ValueError('A plan needs at least one step.')
		plan = Plan(
			plan_id=uuid.uuid4().hex[:8],
			goal=goal,
			steps=[Step(n=i + 1, intent=s) for i, s in enumerate(steps)],
			created_at=time.time(),
		)
		self.plan = plan
		if self.journal:
			self.journal.write('plan_create', plan_id=plan.plan_id, goal=goal, steps=steps)
		return plan

	def require(self) -> Plan:
		if self.plan is None:
			raise NoPlanError('No active plan. Call plan_create(goal, steps) first.')
		return self.plan

	def next_step(self) -> Step | None:
		plan = self.require()
		step = plan.current()
		if step is None:
			plan.status = 'complete'
			if self.journal:
				self.journal.write('plan_complete', plan_id=plan.plan_id)
			return None
		if step.status == 'pending':
			step.status = 'active'
			step.attempts += 1
			if self.journal:
				self.journal.write('step_start', plan_id=plan.plan_id, n=step.n, intent=step.intent, attempt=step.attempts)
		return step

	def observe(self, status: Literal['ok', 'fail', 'skip'], note: str) -> dict[str, Any]:
		plan = self.require()
		step = plan.current()
		if step is None:
			return {'message': 'Plan already complete.', 'plan': self.snapshot()}

		step.observations.append(f'[{status}] {note}')
		if self.journal:
			self.journal.write('step_observe', plan_id=plan.plan_id, n=step.n, status=status, note=note)

		if status == 'ok':
			step.status = 'done'
		elif status == 'skip':
			step.status = 'skipped'
		else:
			step.failures += 1
			if step.failures >= self.MAX_ATTEMPTS_PER_STEP:
				step.status = 'failed'
				return {
					'message': (
						f'Step {step.n} failed {step.failures} times and is now marked failed. '
						f'Call plan_revise() with a different approach — repeating the same actions will not work.'
					),
					'needs_replan': True,
					'plan': self.snapshot(),
				}
			step.status = 'pending'  # retry
			return {
				'message': f'Step {step.n} failed (attempt {step.failures}/{self.MAX_ATTEMPTS_PER_STEP}). Re-read the page state and try a different element or approach.',
				'needs_replan': False,
				'plan': self.snapshot(),
			}

		nxt = plan.current()
		if nxt is None:
			plan.status = 'complete'
			if self.journal:
				self.journal.write('plan_complete', plan_id=plan.plan_id)
			return {'message': f'Step {step.n} recorded. Plan complete.', 'plan': self.snapshot()}
		return {'message': f'Step {step.n} recorded. Next: step {nxt.n} — {nxt.intent}', 'plan': self.snapshot()}

	def revise(self, remaining_steps: list[str], reason: str) -> Plan:
		plan = self.require()
		keep = [s for s in plan.steps if s.status in ('done', 'skipped', 'failed')]
		start = len(keep)
		plan.steps = keep + [Step(n=start + i + 1, intent=s) for i, s in enumerate(remaining_steps)]
		plan.replans += 1
		if self.journal:
			self.journal.write('plan_revise', plan_id=plan.plan_id, reason=reason, remaining=remaining_steps)
		return plan

	def abandon(self, reason: str) -> dict[str, Any]:
		plan = self.require()
		plan.status = 'abandoned'
		if self.journal:
			self.journal.write('plan_abandon', plan_id=plan.plan_id, reason=reason)
		return self.snapshot()

	# -- views --------------------------------------------------------------
	def snapshot(self) -> dict[str, Any]:
		plan = self.require()
		d = asdict(plan)
		d['progress'] = plan.progress()
		return d
