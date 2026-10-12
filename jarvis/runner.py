"""Standalone runner: drive Jarvis with the Claude API instead of an MCP host.

Same brain, same tools, same skills as the MCP path — only the transport and
the caller differ. Use this for unattended runs (cron, CI); use the MCP server
when you want to watch and steer a run interactively.

    pip install anthropic
    export ANTHROPIC_API_KEY=...        # or: ant auth login
    python -m jarvis.runner "search youtube for lofi hip hop and list the top 3"

This is the ONLY file in the package that talks to a model. Everything under
jarvis/ stays LLM-free so the MCP path never needs a key.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

from jarvis.journal import Journal
from jarvis.server import INSTRUCTIONS, JarvisServer

# Two-tier routing: a cheap worker model drives the repetitive plan_next ->
# browser_* -> plan_observe loop (the overwhelming majority of turns, and
# where the token volume actually lives - every plan_next ships the full
# interactive-element list for the page). The supervisor is called only for
# plan_create and for the turn right after a step hard-fails (Brain.observe's
# needs_replan signal, brain.py:112-121) - the only turns that need real
# judgment rather than mechanical execution.
WORKER_MODEL = os.getenv('JARVIS_WORKER_MODEL', os.getenv('JARVIS_MODEL', 'claude-haiku-4-5'))
SUPERVISOR_MODEL = os.getenv('JARVIS_SUPERVISOR_MODEL', 'claude-opus-5')
MAX_TURNS = int(os.getenv('JARVIS_MAX_TURNS', '40'))
MAX_TOKENS = 16000

# Anthropic per-MTok pricing for the journal's cost summary - only the models
# this file defaults to or is likely to be pointed at, not a general price list.
_PRICING_PER_MTOK = {
	'claude-haiku-4-5': (1.00, 5.00),
	'claude-sonnet-5': (2.00, 10.00),
	'claude-opus-5': (5.00, 25.00),
	'claude-fable-5': (10.00, 50.00),
	'claude-fable-5-1': (10.00, 50.00),
}

# Adaptive thinking and the refusal-fallback beta only exist on the 4.6+ tier
# (Opus 5/4.8/4.7/4.6, Sonnet 5/4.6, Fable 5/5.1). Haiku 4.5 errors on both, so
# these are opt-in per model rather than sent unconditionally.
_ADAPTIVE_THINKING_MODELS = {
	'claude-opus-5', 'claude-sonnet-5', 'claude-fable-5', 'claude-fable-5-1', 'claude-mythos-5-1',
	'claude-opus-4-8', 'claude-opus-4-7', 'claude-opus-4-6', 'claude-sonnet-4-6',
}
_REFUSAL_FALLBACK_MODELS = {'claude-opus-5', 'claude-fable-5-1', 'claude-mythos-5-1'}


def _to_anthropic_tools(server: JarvisServer) -> list[dict[str, Any]]:
	return [
		{'name': t.name, 'description': t.description or t.name, 'input_schema': t.input_schema}
		for t in server._tool_list()
	]


def _role_for_turn(turn: int, needs_replan: bool) -> tuple[str, str]:
	"""Supervisor only for the first call (must produce plan_create) and the
	call right after a forced replan; the cheap worker for every mechanical
	plan_next / browser_* / plan_observe turn in between."""
	if turn == 0 or needs_replan:
		return 'supervisor', SUPERVISOR_MODEL
	return 'worker', WORKER_MODEL


def _cost_summary(journal: Journal) -> str:
	"""Per-role token/cost breakdown from the journal's llm_call records - the
	before/after number a worker/supervisor split is meant to produce."""
	totals: dict[str, dict[str, float]] = {}
	for rec in journal.read():
		if rec.get('kind') != 'llm_call':
			continue
		bucket = totals.setdefault(rec['role'], {'calls': 0, 'input': 0, 'output': 0, 'cost': 0.0})
		bucket['calls'] += 1
		bucket['input'] += rec['input_tokens']
		bucket['output'] += rec['output_tokens']
		price = _PRICING_PER_MTOK.get(rec['model'])
		if price:
			bucket['cost'] += rec['input_tokens'] * price[0] / 1_000_000 + rec['output_tokens'] * price[1] / 1_000_000

	lines = ['cost by role:']
	grand_total = 0.0
	for role, b in sorted(totals.items()):
		lines.append(f"  {role}: {int(b['calls'])} calls, {int(b['input'])} in / {int(b['output'])} out tokens, ${b['cost']:.4f}")
		grand_total += b['cost']
	lines.append(f'  total: ${grand_total:.4f}')
	return '\n'.join(lines)


async def run(task: str, headless: bool = False, verbose: bool = True) -> str:
	try:
		from anthropic import AsyncAnthropic
	except ImportError:
		print('The runner needs the anthropic SDK: pip install anthropic', file=sys.stderr)
		raise SystemExit(2)

	# An unset ANTHROPIC_API_KEY does not mean there are no credentials: the SDK
	# also picks up ANTHROPIC_AUTH_TOKEN and `ant auth login` profiles. Let the
	# zero-arg client resolve them and only complain if it actually fails.

	server = JarvisServer(headless=headless)
	client = AsyncAnthropic()
	tools = _to_anthropic_tools(server)
	messages: list[dict[str, Any]] = [{'role': 'user', 'content': task}]
	final = ''
	needs_replan = False

	try:
		for turn in range(MAX_TURNS):
			role, model = _role_for_turn(turn, needs_replan)
			needs_replan = False

			create_kwargs: dict[str, Any] = dict(
				model=model,
				max_tokens=MAX_TOKENS,
				system=INSTRUCTIONS,
				tools=tools,
				messages=messages,
			)
			if model in _ADAPTIVE_THINKING_MODELS:
				# Ask for the summary so the browser run's reasoning is visible as it goes.
				create_kwargs['thinking'] = {'type': 'adaptive', 'display': 'summarized'}
			if model in _REFUSAL_FALLBACK_MODELS:
				# Route around a safety refusal instead of dying mid-run.
				create_kwargs['betas'] = ['server-side-fallback-2026-07-01']
				create_kwargs['fallbacks'] = 'default'

			resp = await client.beta.messages.create(**create_kwargs)

			server.journal.write(
				'llm_call',
				turn=turn,
				role=role,
				model=model,
				input_tokens=resp.usage.input_tokens,
				output_tokens=resp.usage.output_tokens,
			)

			# Always check stop_reason before reading content.
			if resp.stop_reason == 'refusal':
				detail = getattr(resp.stop_details, 'explanation', '') or ''
				final = f'Model declined this task. {detail}'.strip()
				break

			calls = [b for b in resp.content if b.type == 'tool_use']
			text = ''.join(b.text for b in resp.content if b.type == 'text')
			if verbose:
				tag = f'\n[{turn}] ({role}/{model})'
				print(f'{tag} {text}' if text else tag)

			if not calls:
				final = text
				break

			messages.append({'role': 'assistant', 'content': resp.content})
			results = []
			for call in calls:
				if verbose:
					print(f'  -> {call.name}({json.dumps(call.input)[:120]})')
				try:
					out = await server._dispatch(call.name, call.input or {})
					if isinstance(out, list):  # image content: summarise for the text channel
						out = next((c.text for c in out if getattr(c, 'type', '') == 'text'), '[image returned]')
					is_err = False
				except Exception as e:  # surface the failure to the model, do not abort the run
					out, is_err = f'Error in {call.name}: {e}', True
				if call.name == 'plan_observe' and isinstance(out, str):
					try:
						needs_replan = needs_replan or bool(json.loads(out).get('needs_replan'))
					except json.JSONDecodeError:
						pass
				results.append(
					{'type': 'tool_result', 'tool_use_id': call.id, 'content': str(out)[:20000], 'is_error': is_err}
				)
			messages.append({'role': 'user', 'content': results})
		else:
			final = f'Hit the {MAX_TURNS}-turn limit without finishing.'
	finally:
		await server.session.close()

	if verbose:
		print(f'\njournal: {server.journal.path}')
		print(_cost_summary(server.journal))
	return final


def main() -> None:
	ap = argparse.ArgumentParser(description='Run a browser task through the Jarvis brain.')
	ap.add_argument('task', help='What to do, in plain language.')
	ap.add_argument('--headless', action='store_true')
	ap.add_argument('--quiet', action='store_true')
	args = ap.parse_args()

	sys.stdout.reconfigure(encoding='utf-8', errors='replace')
	result = asyncio.run(run(args.task, headless=args.headless, verbose=not args.quiet))
	print('\n=== result ===')
	print(result)


if __name__ == '__main__':
	main()
