"""Jarvis MCP server.

Exposes three tool families over one stdio server:
  browser_*  - the full browser-use action registry, schemas auto-generated
  plan_*     - the brain: a plan state machine the host must drive explicitly
  skill_*    - domain knowledge, injected at the moment of acting

There is no LLM in this process. The MCP host is the reasoning engine.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import uuid
from typing import Any

os.environ.setdefault('BROWSER_USE_LOGGING_LEVEL', 'critical')
os.environ.setdefault('BROWSER_USE_SETUP_LOGGING', 'false')
os.environ.setdefault('ANONYMIZED_TELEMETRY', 'false')

logging.basicConfig(stream=sys.stderr, level=logging.WARNING, force=True)

import mcp.server.stdio  # noqa: E402
import mcp.types as types  # noqa: E402
from mcp.server import Server  # noqa: E402

from browser_use.tools.service import Tools  # noqa: E402

from jarvis import __version__  # noqa: E402
from jarvis.brain import Brain, NoPlanError  # noqa: E402
from jarvis.journal import Journal  # noqa: E402
from jarvis.session import Session  # noqa: E402
from jarvis.skills import SkillLibrary  # noqa: E402
from jarvis.tools import action_name, build_action_specs  # noqa: E402

logger = logging.getLogger('jarvis')

INSTRUCTIONS = """Jarvis drives a real browser through browser-use's core tools.

You are the reasoning layer. Work a task like this:

1. plan_create(goal, steps)  - commit to a plan before touching the browser.
2. plan_next()               - returns the current step, the matching domain
                               skill, and the LIVE page state in one call.
                               Always act on the element indices it returns.
3. browser_*                 - take exactly the actions that step needs.
4. plan_observe(status,note) - record what actually happened. This advances
                               the plan. Never skip it.

Repeat 2-4 until plan_next() reports the plan is complete. If a step fails
three times, plan_revise() with a genuinely different approach.

Element indices come from page state and change after every navigation or
click. Never reuse an index across steps without a fresh plan_next().
"""


class JarvisServer:
	def __init__(self, headless: bool = False, allowed_domains: list[str] | None = None):
		self.server: Server = Server('jarvis', version=__version__, instructions=INSTRUCTIONS)
		self.session = Session(headless=headless, allowed_domains=allowed_domains)
		self.run_id = uuid.uuid4().hex[:8]
		self.journal = Journal(self.run_id)
		self.brain = Brain(journal=self.journal)
		self.skills = SkillLibrary()
		self.action_specs = build_action_specs(Tools())
		self._setup()

	# -- tool catalogue -----------------------------------------------------
	def _tool_list(self) -> list[types.Tool]:
		tools: list[types.Tool] = []

		# The brain comes first: it is what the host should reach for first.
		tools.append(
			types.Tool(
				name='plan_create',
				description=(
					'Commit to a plan before acting. Break the goal into small, verifiable steps, '
					'each one a single observable outcome ("type the query into the search box"), '
					'not a whole workflow. Call this before any browser_ tool.'
				),
				input_schema={
					'type': 'object',
					'properties': {
						'goal': {'type': 'string', 'description': 'The overall objective in one sentence.'},
						'steps': {
							'type': 'array',
							'items': {'type': 'string'},
							'description': 'Ordered list of step intents.',
						},
					},
					'required': ['goal', 'steps'],
				},
			)
		)
		tools.append(
			types.Tool(
				name='plan_next',
				description=(
					'Get the current step together with the domain skill for the current page and '
					'the live page state (URL, title, interactive element indices). Call this before '
					'every action - indices from earlier calls go stale after navigation or clicks.'
				),
				input_schema={
					'type': 'object',
					'properties': {
						'include_screenshot': {'type': 'boolean', 'default': False},
					},
				},
			)
		)
		tools.append(
			types.Tool(
				name='plan_observe',
				description=(
					'Record what actually happened on the current step and advance the plan. '
					'status="ok" completes it, "fail" retries it (3 attempts, then forces a revise), '
					'"skip" marks it unnecessary. Always call this after acting.'
				),
				input_schema={
					'type': 'object',
					'properties': {
						'status': {'type': 'string', 'enum': ['ok', 'fail', 'skip']},
						'note': {
							'type': 'string',
							'description': 'What you observed - concrete, not a restatement of intent.',
						},
					},
					'required': ['status', 'note'],
				},
			)
		)
		tools.append(
			types.Tool(
				name='plan_revise',
				description='Replace all remaining steps with a new approach. Use after a step has hard-failed.',
				input_schema={
					'type': 'object',
					'properties': {
						'remaining_steps': {'type': 'array', 'items': {'type': 'string'}},
						'reason': {'type': 'string', 'description': 'Why the old approach did not work.'},
					},
					'required': ['remaining_steps', 'reason'],
				},
			)
		)
		tools.append(
			types.Tool(
				name='plan_status',
				description='Full plan trace: every step, its status, attempts and observations.',
				input_schema={'type': 'object', 'properties': {}},
				annotations=types.ToolAnnotations(read_only_hint=True),
			)
		)

		# Native page introspection (no LLM).
		tools.append(
			types.Tool(
				name='browser_state',
				description=(
					'Live page state: URL, title, tabs, and indexed interactive elements. '
					'Indices feed browser_click and browser_type.'
				),
				input_schema={
					'type': 'object',
					'properties': {'include_screenshot': {'type': 'boolean', 'default': False}},
				},
				annotations=types.ToolAnnotations(read_only_hint=True),
			)
		)
		tools.append(
			types.Tool(
				name='browser_screenshot',
				description='Screenshot of the current page, returned inline as an image.',
				input_schema={
					'type': 'object',
					'properties': {'full_page': {'type': 'boolean', 'default': False}},
				},
				annotations=types.ToolAnnotations(read_only_hint=True),
			)
		)
		tools.append(
			types.Tool(
				name='browser_markdown',
				description=(
					'The page as clean markdown for you to read directly. '
					'Replaces the LLM-backed extract action.'
				),
				input_schema={
					'type': 'object',
					'properties': {'extract_links': {'type': 'boolean', 'default': False}},
				},
				annotations=types.ToolAnnotations(read_only_hint=True),
			)
		)
		tools.append(
			types.Tool(
				name='browser_html',
				description='Raw HTML of the page, or of one element by CSS selector.',
				input_schema={
					'type': 'object',
					'properties': {'selector': {'type': 'string'}},
				},
				annotations=types.ToolAnnotations(read_only_hint=True),
			)
		)

		# Skills.
		tools.append(
			types.Tool(
				name='skill_list',
				description='Domains that have a stored skill.',
				input_schema={'type': 'object', 'properties': {}},
				annotations=types.ToolAnnotations(read_only_hint=True),
			)
		)
		tools.append(
			types.Tool(
				name='skill_get',
				description='Read a domain skill in full.',
				input_schema={
					'type': 'object',
					'properties': {'name': {'type': 'string', 'description': 'e.g. youtube.com'}},
					'required': ['name'],
				},
				annotations=types.ToolAnnotations(read_only_hint=True),
			)
		)
		tools.append(
			types.Tool(
				name='skill_learn',
				description=(
					'Append a lesson to a domain skill so the next run starts smarter. '
					'Record concrete, reusable facts (a selector that worked, an interstitial to expect).'
				),
				input_schema={
					'type': 'object',
					'properties': {
						'domain': {'type': 'string'},
						'lesson': {'type': 'string'},
					},
					'required': ['domain', 'lesson'],
				},
			)
		)

		# Everything in the browser-use action registry.
		for name, spec in sorted(self.action_specs.items()):
			tools.append(types.Tool(name=name, description=spec['description'], input_schema=spec['input_schema']))
		return tools

	# -- dispatch -----------------------------------------------------------
	async def _dispatch(self, name: str, args: dict[str, Any]) -> str | list[types.ContentBlock]:
		if name == 'plan_create':
			plan = self.brain.create(args['goal'], args['steps'])
			return json.dumps(
				{'plan_id': plan.plan_id, 'steps': len(plan.steps), 'next': 'Call plan_next() to begin.'},
				indent=2,
			)

		if name == 'plan_next':
			step = self.brain.next_step()
			if step is None:
				return json.dumps({'complete': True, 'plan': self.brain.snapshot()}, indent=2, default=str)
			state, shot = await self.session.state(include_screenshot=bool(args.get('include_screenshot')))
			plan = self.brain.require()
			payload: dict[str, Any] = {
				'step': {
					'n': step.n,
					'intent': step.intent,
					'attempt': step.attempts,
					'previous_observations': step.observations,
				},
				'progress': plan.progress(),
				'goal': plan.goal,
				'page': state,
			}
			match = self.skills.for_url(state.get('url', ''))
			if match:
				payload['skill'] = {'domain': match[0], 'body': match[1]}
			else:
				payload['skill'] = None
				payload['skill_hint'] = f'No skill for this domain. Available: {self.skills.available()}'
			text = json.dumps(payload, indent=2)
			if shot:
				return [
					types.TextContent(type='text', text=text),
					types.ImageContent(type='image', data=shot, mimeType='image/png'),
				]
			return text

		if name == 'plan_observe':
			return json.dumps(self.brain.observe(args['status'], args['note']), indent=2, default=str)

		if name == 'plan_revise':
			plan = self.brain.revise(args['remaining_steps'], args['reason'])
			return json.dumps(
				{'plan_id': plan.plan_id, 'replans': plan.replans, 'plan': self.brain.snapshot()},
				indent=2,
				default=str,
			)

		if name == 'plan_status':
			return json.dumps(self.brain.snapshot(), indent=2, default=str)

		if name == 'browser_state':
			state, shot = await self.session.state(include_screenshot=bool(args.get('include_screenshot')))
			text = json.dumps(state, indent=2)
			if shot:
				return [
					types.TextContent(type='text', text=text),
					types.ImageContent(type='image', data=shot, mimeType='image/png'),
				]
			return text

		if name == 'browser_screenshot':
			data = await self.session.screenshot_b64(full_page=bool(args.get('full_page')))
			return [types.ImageContent(type='image', data=data, mimeType='image/png')]

		if name == 'browser_markdown':
			return await self.session.markdown(extract_links=bool(args.get('extract_links')))

		if name == 'browser_html':
			return await self.session.html(args.get('selector'))

		if name == 'skill_list':
			return json.dumps({'skills': self.skills.available()}, indent=2)

		if name == 'skill_get':
			body = self.skills.get(args['name'])
			if body:
				return body
			return 'No skill named {}. Available: {}'.format(args['name'], self.skills.available())

		if name == 'skill_learn':
			path = self.skills.append_lesson(args['domain'], args['lesson'])
			self.journal.write('skill_learn', domain=args['domain'], lesson=args['lesson'])
			return f'Recorded to {path}'

		if name in self.action_specs:
			result = await self.session.run_action(action_name(name), args)
			self.journal.write('action', tool=name, args=args, result=str(result)[:2000])
			return self._format_action_result(result)

		return f'Unknown tool: {name}'

	@staticmethod
	def _format_action_result(result: Any) -> str:
		"""ActionResult -> the text the host should see."""
		err = getattr(result, 'error', None)
		if err:
			return f'Error: {err}'
		for attr in ('extracted_content', 'long_term_memory'):
			val = getattr(result, attr, None)
			if val:
				return str(val)
		return str(result)

	def _setup(self) -> None:
		async def handle_list_tools(_ctx: Any, _params: types.PaginatedRequestParams) -> types.ListToolsResult:
			return types.ListToolsResult(tools=self._tool_list())

		async def handle_list_resources(_ctx: Any, _p: types.PaginatedRequestParams) -> types.ListResourcesResult:
			return types.ListResourcesResult(resources=[])

		async def handle_list_prompts(_ctx: Any, _p: types.PaginatedRequestParams) -> types.ListPromptsResult:
			return types.ListPromptsResult(prompts=[])

		async def handle_call_tool(_ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
			try:
				result = await self._dispatch(params.name, params.arguments or {})
				if isinstance(result, list):
					return types.CallToolResult(content=result)
				return types.CallToolResult(content=[types.TextContent(type='text', text=result)])
			except NoPlanError as e:
				return types.CallToolResult(content=[types.TextContent(type='text', text=str(e))], is_error=True)
			except Exception as e:
				logger.exception('tool failed: %s', params.name)
				self.journal.write('tool_error', tool=params.name, error=str(e))
				return types.CallToolResult(
					content=[types.TextContent(type='text', text=f'Error in {params.name}: {e}')],
					is_error=True,
				)

		self.server.add_request_handler('tools/list', types.PaginatedRequestParams, handle_list_tools)
		self.server.add_request_handler('resources/list', types.PaginatedRequestParams, handle_list_resources)
		self.server.add_request_handler('prompts/list', types.PaginatedRequestParams, handle_list_prompts)
		self.server.add_request_handler('tools/call', types.CallToolRequestParams, handle_call_tool)

	async def run(self) -> None:
		async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
			try:
				await self.server.run(read_stream, write_stream, self.server.create_initialization_options())
			except BrokenPipeError:
				logger.warning('client disconnected')
			finally:
				await self.session.close()


async def main() -> None:
	headless = os.getenv('JARVIS_HEADLESS', '').lower() in ('1', 'true', 'yes')
	domains = [d.strip() for d in os.getenv('JARVIS_ALLOWED_DOMAINS', '').split(',') if d.strip()]
	await JarvisServer(headless=headless, allowed_domains=domains or None).run()


if __name__ == '__main__':
	asyncio.run(main())
