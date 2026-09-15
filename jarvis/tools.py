"""Tool surface: browser-use's action registry, exposed wholesale over MCP.

Schemas are generated from the registry's Pydantic param models rather than
hand-written, so the surface cannot drift from the library and new upstream
actions appear automatically.
"""

from __future__ import annotations

from typing import Any

from browser_use.tools.service import Tools

# Actions deliberately not exposed.
#   done    - belongs to the agent loop; the host decides when a task is done.
#   extract - requires an LLM. browser_markdown returns the same page content
#             and lets the host read it, keeping this package LLM-free.
#   screenshot - registry version writes to disk; we return the image inline.
EXCLUDED = {'done', 'extract', 'screenshot'}

# Actions renamed for a clearer tool namespace.
RENAMES = {
	'input': 'type',
	'click': 'click',
	'switch': 'switch_tab',
	'close': 'close_tab',
}

PREFIX = 'browser_'


def mcp_name(action_name: str) -> str:
	return PREFIX + RENAMES.get(action_name, action_name)


def action_name(tool_name: str) -> str:
	bare = tool_name[len(PREFIX) :] if tool_name.startswith(PREFIX) else tool_name
	for action, renamed in RENAMES.items():
		if renamed == bare:
			return action
	return bare


def _clean_schema(model: Any) -> dict[str, Any]:
	"""Pydantic JSON schema -> MCP input schema."""
	schema = model.model_json_schema()
	schema.pop('title', None)
	for prop in schema.get('properties', {}).values():
		prop.pop('title', None)
	schema.setdefault('type', 'object')
	schema.setdefault('properties', {})
	return schema


def build_action_specs(tools: Tools) -> dict[str, dict[str, Any]]:
	"""Map MCP tool name -> {action, description, input_schema}."""
	specs: dict[str, dict[str, Any]] = {}
	for name, action in tools.registry.registry.actions.items():
		if name in EXCLUDED:
			continue
		specs[mcp_name(name)] = {
			'action': name,
			'description': (action.description or name).strip(),
			'input_schema': _clean_schema(action.param_model),
		}
	return specs
