"""Browser session lifecycle. Deliberately contains no LLM.

browser-use's Tools() registry and BrowserSession run entirely over CDP; the
only action that ever needed a model was `extract`, which we do not expose.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from browser_use.browser.profile import BrowserProfile
from browser_use.browser.session import BrowserSession
from browser_use.filesystem.file_system import FileSystem
from browser_use.tools.service import Tools

DEFAULT_PROFILE_DIR = Path.home() / '.jarvis' / 'profile'
DEFAULT_FS_DIR = Path.home() / '.jarvis' / 'files'


class Session:
	"""Owns one browser session plus the tool registry bound to it."""

	def __init__(self, headless: bool = False, allowed_domains: list[str] | None = None):
		self.headless = headless
		self.allowed_domains = allowed_domains
		self.browser: BrowserSession | None = None
		self.tools: Tools | None = None
		self.file_system: FileSystem | None = None

	async def ensure(self) -> BrowserSession:
		if self.browser is not None:
			return self.browser

		profile_data: dict[str, Any] = {
			'downloads_path': str(Path.home() / 'Downloads' / 'jarvis'),
			'user_data_dir': str(DEFAULT_PROFILE_DIR),
			'wait_between_actions': 0.4,
			'keep_alive': True,
			'headless': self.headless,
		}
		if self.allowed_domains:
			profile_data['allowed_domains'] = self.allowed_domains

		self.browser = BrowserSession(browser_profile=BrowserProfile(**profile_data))
		await self.browser.start()
		self.tools = Tools()
		self.file_system = FileSystem(base_dir=DEFAULT_FS_DIR)
		return self.browser

	# -- introspection ------------------------------------------------------
	async def state(self, include_screenshot: bool = False) -> tuple[dict[str, Any], str | None]:
		browser = await self.ensure()
		summary = await browser.get_browser_state_summary()

		result: dict[str, Any] = {
			'url': summary.url,
			'title': summary.title,
			'tabs': [{'url': t.url, 'title': t.title} for t in summary.tabs],
			'interactive_elements': [],
		}
		if summary.page_info:
			pi = summary.page_info
			result['viewport'] = {'width': pi.viewport_width, 'height': pi.viewport_height}
			result['scroll'] = {'x': pi.scroll_x, 'y': pi.scroll_y}
			result['page'] = {'width': pi.page_width, 'height': pi.page_height}

		for index, el in summary.dom_state.selector_map.items():
			info: dict[str, Any] = {
				'index': index,
				'tag': el.tag_name,
				'text': el.get_all_children_text(max_depth=2)[:100],
			}
			for attr in ('placeholder', 'href', 'aria-label', 'id', 'name', 'type'):
				val = el.attributes.get(attr)
				if val:
					info[attr] = val
			result['interactive_elements'].append(info)

		shot = summary.screenshot if (include_screenshot and summary.screenshot) else None
		return result, shot

	async def current_url(self) -> str:
		browser = await self.ensure()
		try:
			summary = await browser.get_browser_state_summary(cached=True)
			return summary.url or ''
		except Exception:
			return ''

	async def html(self, selector: str | None = None) -> str:
		browser = await self.ensure()
		cdp = await browser.get_or_create_cdp_session(target_id=None, focus=False)
		if selector:
			expr = f'(function(){{const e=document.querySelector({json.dumps(selector)});return e?e.outerHTML:null;}})()'
		else:
			expr = 'document.documentElement.outerHTML'
		res = await cdp.cdp_client.send.Runtime.evaluate(
			params={'expression': expr, 'returnByValue': True}, session_id=cdp.session_id
		)
		value = res.get('result', {}).get('value')
		return value if value is not None else f'No element matched selector: {selector}'

	async def markdown(self, extract_links: bool = False) -> str:
		"""Clean page markdown, with no LLM in the loop."""
		from browser_use.dom.markdown_extractor import extract_clean_markdown

		browser = await self.ensure()
		content, _stats = await extract_clean_markdown(
			browser_session=browser, extract_links=extract_links, extract_images=False
		)
		return content

	async def screenshot_b64(self, full_page: bool = False) -> str:
		browser = await self.ensure()
		cdp = await browser.get_or_create_cdp_session(target_id=None, focus=True)
		params: dict[str, Any] = {'format': 'png', 'captureBeyondViewport': full_page}
		res = await cdp.cdp_client.send.Page.captureScreenshot(params=params, session_id=cdp.session_id)
		return res['data']

	# -- actions ------------------------------------------------------------
	async def run_action(self, name: str, params: dict[str, Any]) -> Any:
		await self.ensure()
		assert self.tools is not None
		return await self.tools.registry.execute_action(
			action_name=name,
			params=params,
			browser_session=self.browser,
			file_system=self.file_system,
		)

	async def close(self) -> None:
		if self.browser is not None:
			try:
				await self.browser.kill()
			except Exception:
				pass
			self.browser = None
			self.tools = None
