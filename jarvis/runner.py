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

from jarvis.server import INSTRUCTIONS, JarvisServer

MODEL = os.getenv('JARVIS_MODEL', 'claude-opus-5')
MAX_TURNS = int(os.getenv('JARVIS_MAX_TURNS', '40'))
MAX_TOKENS = 16000


def _to_anthropic_tools(server: JarvisServer) -> list[dict[str, Any]]:
	return [
		{'name': t.name, 'description': t.description or t.name, 'input_schema': t.input_schema}
		for t in server._tool_list()
	]


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

	try:
		for turn in range(MAX_TURNS):
			resp = await client.beta.messages.create(
				model=MODEL,
				max_tokens=MAX_TOKENS,
				system=INSTRUCTIONS,
				tools=tools,
				messages=messages,
				# Adaptive thinking is on by default for Opus 5; asking for the
				# summary makes the browser run's reasoning visible as it goes.
				thinking={'type': 'adaptive', 'display': 'summarized'},
				# Route around a safety refusal instead of dying mid-run.
				betas=['server-side-fallback-2026-07-01'],
				fallbacks='default',
			)

			# Always check stop_reason before reading content.
			if resp.stop_reason == 'refusal':
				detail = getattr(resp.stop_details, 'explanation', '') or ''
				final = f'Model declined this task. {detail}'.strip()
				break

			calls = [b for b in resp.content if b.type == 'tool_use']
			text = ''.join(b.text for b in resp.content if b.type == 'text')
			if text and verbose:
				print(f'\n[{turn}] {text}')

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
