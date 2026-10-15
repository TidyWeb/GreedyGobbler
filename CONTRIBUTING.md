# Contributing to Greedy Gobbler

Thanks for your interest. Greedy Gobbler is a small personal tool — contributions are welcome but kept simple.

## Reporting bugs

Open a GitHub issue and include:
- What you did (URL submitted, file type dropped, etc.)
- What you expected to happen
- What actually happened (paste the status bar message if there is one)

## Submitting a PR

1. Fork the repo and create a branch from `main`
2. Keep changes focused — one fix or feature per PR
3. Install the runtime and development requirements, then run the checks below
4. Describe what you changed and why in the PR description

## Local checks

Create an isolated environment and install the direct and test dependencies:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt
```

Exact token-count tests need the tokenizer data stored locally. Populate it explicitly and keep the same cache path for the test run:

```bash
TOKEN_CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/greedygobbler/tokenizers"
mkdir -p "$TOKEN_CACHE"
TIKTOKEN_CACHE_DIR="$TOKEN_CACHE" .venv/bin/python -c "import tiktoken; tiktoken.get_encoding('cl100k_base'); tiktoken.get_encoding('o200k_base')"
export TIKTOKEN_CACHE_DIR="$TOKEN_CACHE"
```

Run the deterministic checks from the repository root:

```bash
.venv/bin/python -m unittest discover -s tests -v
node --test tests/test_ui.cjs
node --check static/app.js
.venv/bin/python -m compileall -q app.py converter.py pipeline tests benchmarks
.venv/bin/python -m pip check
git diff --check
```

The test suite creates retained synthetic input and output files under the ignored `.test-artifacts/` folder. It does not contact the network. Tests that need an optional live browser target or local compression model are skipped unless those inputs are explicitly configured; see [development setup](docs/development.md).

No formal style guide. Just match the existing code's tone and simplicity.

## Known limitations

**ODT files are not supported.** MarkItDown has no ODT converter, and there is no plan to add a custom one. Please don't open issues or PRs for ODT support.

Some ebook formats require external tools: MOBI/AZW/FB2/LRF/HTMLZ need Calibre's `ebook-convert`, and DjVu files need `djvutxt`. If reporting one of those paths, include whether the relevant tool is installed.
