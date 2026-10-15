# Development setup

## Local installation

Use Python 3.11 or newer and Node.js for the JavaScript checks. PDF OCR also needs Poppler and Tesseract on the system.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt
```

Exact tokenizer counts need local `cl100k_base` or `o200k_base` data. Populate the cache explicitly before starting the app:

```bash
TOKEN_CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/greedygobbler/tokenizers"
mkdir -p "$TOKEN_CACHE"
TIKTOKEN_CACHE_DIR="$TOKEN_CACHE" \
  .venv/bin/python -c "import tiktoken; tiktoken.get_encoding('cl100k_base'); tiktoken.get_encoding('o200k_base')"
TIKTOKEN_CACHE_DIR="$TOKEN_CACHE" .venv/bin/python app.py
```

The two tokenizer data files are fetched only by that explicit setup command. The application does not download tokenizer data or model weights at runtime. Use `utf8_bytes` when exact token data is unavailable; the interface labels that result as a byte bound.

## Tests and retained artifacts

Run from the repository root:

```bash
.venv/bin/python -m unittest discover -s tests -v
node --test tests/test_ui.cjs
node --check static/app.js
.venv/bin/python -m compileall -q app.py converter.py pipeline tests benchmarks
.venv/bin/python -m pip check
git diff --check
```

Synthetic source documents, uploaded copies and test saves are retained under `.test-artifacts/`. The integration tests do not delete files and do not make external requests. The website route test uses an injected fixture response and a test-only resolver.

The offline benchmark writes its JSON output to a path supplied by the caller:

```bash
TIKTOKEN_CACHE_DIR="$TOKEN_CACHE" \
  .venv/bin/python benchmarks/context_benchmark.py \
  --output .test-artifacts/benchmarks/context-benchmark.json
```

Its phrase-retention and query-coverage measures are lexical proxies. They do not measure generated answer accuracy.

## Optional browser extraction

Install the browser packages and their browser runtime explicitly:

```bash
.venv/bin/python -m pip install -r requirements-browser.txt
.venv/bin/crawl4ai-setup
```

Browser extraction is off by default. To opt into the live browser integration test, set `GREEDYGOBBLER_TEST_BROWSER_URL` to a public JavaScript-rendered test page you are permitted to fetch and `GREEDYGOBBLER_TEST_BROWSER_MARKER` to text inserted by that page's script, then run:

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_optional_integrations.py' -v
```

The test is skipped unless the URL, expected marker and Crawl4AI are available. It asserts the script-only marker appears in extracted text, so a successful static HTML fetch cannot satisfy the check.

## Optional learned compression

Install the pinned optional packages, then set `COMPRESSOR_MODEL` to a model directory that is already present on the server:

```bash
.venv/bin/python -m pip install -r requirements-compression.txt
COMPRESSOR_MODEL="/path/to/a/local/model" .venv/bin/python app.py
```

The model path is server configuration. The application does not download it. The compressor remains experimental; see [compression evaluation](compression-evaluation.md). Set `GREEDYGOBBLER_TEST_MODEL` to that local directory to opt into the model-backed integration check. Without the environment variable and installed package, that check reports a clear skip.
