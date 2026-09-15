"""Domain skill loader.

A skill is a markdown file named after the domain it covers (youtube.com.md).
The brain injects the matching skill into plan_next() so the host LLM sees
site-specific knowledge at the moment it is about to act, not before.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import urlparse

# JARVIS_SKILLS_DIR lets tests (and alternate skill sets) point somewhere else
# so a run never mutates the shipped skills.
SKILLS_DIR = Path(os.getenv('JARVIS_SKILLS_DIR') or Path(__file__).parent / 'skills')


def _domain_of(url: str) -> str:
	try:
		host = urlparse(url).netloc.lower()
	except ValueError:
		return ''
	return host[4:] if host.startswith('www.') else host


class SkillLibrary:
	def __init__(self, skills_dir: Path | None = None):
		self.skills_dir = skills_dir or SKILLS_DIR
		self.skills_dir.mkdir(parents=True, exist_ok=True)

	def available(self) -> list[str]:
		return sorted(p.stem for p in self.skills_dir.glob('*.md'))

	def for_url(self, url: str) -> tuple[str, str] | None:
		"""Return (skill_name, body) for the most specific skill matching url."""
		domain = _domain_of(url)
		if not domain:
			return None
		# Most specific match wins: news.ycombinator.com beats ycombinator.com.
		best: tuple[str, str] | None = None
		for name in self.available():
			if domain == name or domain.endswith('.' + name):
				body = (self.skills_dir / f'{name}.md').read_text(encoding='utf-8')
				if best is None or len(name) > len(best[0]):
					best = (name, body)
		return best

	def for_task(self, task: str) -> list[str]:
		"""Names of skills whose domain is mentioned anywhere in a task string."""
		low = task.lower()
		return [n for n in self.available() if n in low or n.split('.')[0] in re.findall(r'[a-z0-9]+', low)]

	def get(self, name: str) -> str | None:
		path = self.skills_dir / f'{name}.md'
		return path.read_text(encoding='utf-8') if path.exists() else None

	def append_lesson(self, name: str, lesson: str) -> str:
		"""Append a learned lesson so the next run starts smarter than this one."""
		path = self.skills_dir / f'{name}.md'
		if not path.exists():
			path.write_text(f'# {name}\n\n## Lessons\n', encoding='utf-8')
		body = path.read_text(encoding='utf-8')
		if '## Lessons' not in body:
			body += '\n\n## Lessons\n'
		body = body.rstrip() + f'\n- {lesson.strip()}\n'
		path.write_text(body, encoding='utf-8')
		return str(path)
