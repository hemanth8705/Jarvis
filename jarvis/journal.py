"""Append-only run journal.

Every plan, step and action outcome is written as one JSON object per line so a
run can be replayed, diffed, or mined for new skills after the fact.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

DEFAULT_JOURNAL_DIR = Path.home() / '.jarvis' / 'runs'


class Journal:
	def __init__(self, run_id: str, base_dir: Path | None = None):
		self.run_id = run_id
		self.base_dir = base_dir or DEFAULT_JOURNAL_DIR
		self.base_dir.mkdir(parents=True, exist_ok=True)
		self.path = self.base_dir / f'{run_id}.jsonl'

	def write(self, kind: str, **fields: Any) -> None:
		record = {'ts': round(time.time(), 3), 'run_id': self.run_id, 'kind': kind, **fields}
		with self.path.open('a', encoding='utf-8') as fh:
			fh.write(json.dumps(record, ensure_ascii=False, default=str) + '\n')

	def read(self) -> list[dict[str, Any]]:
		if not self.path.exists():
			return []
		out: list[dict[str, Any]] = []
		for line in self.path.read_text(encoding='utf-8').splitlines():
			line = line.strip()
			if not line:
				continue
			try:
				out.append(json.loads(line))
			except json.JSONDecodeError:
				continue
		return out
