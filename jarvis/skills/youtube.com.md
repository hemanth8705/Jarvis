# youtube.com

Video search and playback.

## Search flow
1. `browser_navigate` to `https://www.youtube.com`.
2. `browser_state` — the search box is an `input#search` with
   `placeholder="Search"`. It is usually one of the first interactive indices.
3. `browser_input` the query into that index.
4. `browser_send_keys` with `Enter`. Clicking the magnifying glass also works
   but the button index shifts once the suggestion dropdown opens, so Enter is
   more reliable.
5. `browser_state` again — results are `ytd-video-renderer` blocks. The first
   result title link is the first `a#video-title` in the element list.

## Gotchas
- A consent/cookie interstitial can appear on a cold profile. If the state shows
  "Before you continue to YouTube", accept it before searching.
- The suggestion dropdown overlays the results; `Escape` closes it.
- Autoplay is often blocked until the page has focus. Clicking the video
  thumbnail (not just the title) is the more reliable way to start playback.
- Titles in `browser_state` are truncated to 100 chars. Use
  `browser_find_elements` with selector `a#video-title` and
  `attributes=["title","href"]` to get full titles and URLs.

## Verifying playback
`browser_evaluate` with:
```js
(function(){ const v=document.querySelector('video');
  return v ? {paused:v.paused, t:v.currentTime, src:location.href} : 'no video'; })()
```
