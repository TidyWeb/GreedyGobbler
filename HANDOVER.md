# Project: Greedy Gobbler Handover

## Context
- **Display name**: Greedy Gobbler (folder: DirtyGobbler — do not rename)
- **Parent project**: Gobbler at `/home/[user]/projects/Predigester/` — do not modify that project
- **Platform**: Fedora 43 KDE Plasma
- **Tools**: Python 3.13, Flask, MarkItDown, requests + BeautifulSoup + markdownify
- **Environment**: Virtual environment at `/home/[user]/projects/DirtyGobbler/.venv`
- **Runs at**: http://127.0.0.1:5000/

## What is Greedy Gobbler?
A lightweight single-page URL fetcher variant of Gobbler. Replaces Crawl4AI (headless browser, ~1.5 GB) with a simple `requests` + `markdownify` fetch. Trade-off: no JavaScript rendering, no multi-page crawling, but near-instant startup and a tiny dependency footprint.

## Current Architecture

```
app.py              Flask server — routes: /, /process, /download (no /progress)
converter.py        Orchestration: MarkItDown (files) + requests/markdownify (URLs) + normaliser
pipeline/
  normaliser.py     Deterministic Markdown cleaning pipeline (nav, boilerplate, duplicates)
  ai_compressor.py  Stub — reserved for Ollama integration (deferred)
static/
  index.html        Full UI — all styles inline, Lato font via Google Fonts
  app.js            Frontend: drag-drop, URL submission, Raw/Normalised toggle (no progress polling)
  gobbler.png       Mandala turkey logo (mix-blend-mode: multiply on cream background)
  style.css         Legacy — not served by current app.py
templates/
  index.html        Legacy scaffold — not served by current app.py
launch.sh           KDE launcher: starts Flask if not running, opens browser
requirements.txt    All direct deps (Flask, markitdown, pytesseract, Pillow, requests, bs4, markdownify)
```

**Saving**: Output written to `/home/[user]/Downloads/DirtyGobbler/` as `.md` files.

## Key Technical Decisions
- **URL fetching**: `requests.get()` with a browser-like User-Agent, 15s timeout. Response HTML converted to Markdown via `markdownify(html, heading_style="ATX")`. Single page only — no depth, no crawling.
- **Format support**: MarkItDown 0.1.5 with extras `[pdf,docx,xlsx,pptx]` installed. Confirmed working: PDF, DOCX, XLSX, PPTX, CSV, TXT, HTML. ODT is unsupported — MarkItDown has no ODT converter; `odfpy` would not help.
- **RTF**: Custom path in `converter.py` — detected by `.rtf` extension, decoded via `striprtf.striprtf.rtf_to_text()`, bypasses MarkItDown entirely. MarkItDown has no RTF converter.
- **Image OCR**: JPG/JPEG/PNG intercepted in `converter.py` before MarkItDown, processed via `pytesseract.image_to_string()`. `tesseract` 5.5.2 at `/usr/bin/tesseract`. MarkItDown's own ImageConverter does not do OCR (EXIF + LLM only, neither configured).
- **No `/progress` endpoint** — synchronous fetch needs no polling.
- **`process_input()` returns `(raw, cleaned)` tuple** — both sent to frontend; Raw/Normalised toggle switches between them.
- **Normaliser** protects fenced code blocks, strips: bare-link nav blocks (≥3 consecutive), boilerplate phrases, duplicate paragraphs; collapses blank lines; strips trailing whitespace.
- **URL filename logic** (`buildFileName` in `app.js`): hostname → strip `www.` → take first label as domain; last non-empty path segment (extension stripped) as subject; combines as `domain-subject.md`. Falls back to `domain-N.md` (session counter).
- **Paywall detection** (`isPaywalled` in `app.js`): runs on cleaned content after URL fetches. Triggers if content < 200 chars or matches paywall phrases. Amber warning in status bar.

## Visual Design
- Starbucks-inspired palette: `#1e3932` (headings/text), `#006241` (primary buttons), `#cba258` (gold toggle active), `#f2f0eb` (warm cream background)
- Font: Lato 400/700 via Google Fonts
- All interactive elements pill-shaped (`border-radius: 50px`)
- Logo: mandala turkey PNG with `mix-blend-mode: multiply`

## Instructions for New Chat
1. Read this file before doing anything.
2. Do NOT modify the original Gobbler project at `/home/[user]/projects/Predigester/`.
3. Present a plan and wait for approval before writing any code on non-trivial changes.
4. Do not rename files or folders — only display strings.
5. Do not add features, comments, or error handling beyond what is asked.
