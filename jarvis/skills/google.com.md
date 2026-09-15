# google.com

Web search.

## Search flow
1. `browser_navigate` to `https://www.google.com`.
2. The search box is `textarea[name="q"]` (a textarea, not an input, since 2023).
3. `browser_input` the query, then `browser_send_keys` `Enter`.
4. Results are `div#search a[href]` with an `h3` title inside.

## Gotchas
- A consent dialog appears in the EU and on cold profiles. Accept it first.
- `browser_search` (the core action) goes straight to a results URL and skips
  the homepage entirely — prefer it when you only need results.
- Grab results without an LLM using `browser_find_elements` with selector
  `div#search h3` and `include_text=true`.
