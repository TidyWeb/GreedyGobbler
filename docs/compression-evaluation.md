# Compression evaluation

The benchmark separates three operations that are easy to conflate:

1. **Raw extraction** is the source wording produced by the local parser.
2. **Conservative normalisation** is `pipeline.normaliser.clean()`. It reduces formatting clutter and selected exact interface labels; it does not rewrite claims into shorter prose.
3. **Context preparation** compresses eligible prose blocks first when LLMLingua-2 is enabled, then selects and formats source-backed evidence to fit a budget. This path remains experimental.

`benchmarks/fixtures/context_cleaned.md` is a manually edited compact field-note fixture. It exists to regress packing of a short note; it is not the output of the normaliser. The benchmark now measures the raw field note after an actual `clean()` call separately from that hand-edited fixture.

Run the offline benchmark with a verified local `cl100k_base` cache:

```bash
TIKTOKEN_CACHE_DIR="$TOKEN_CACHE" \
  .venv/bin/python benchmarks/context_benchmark.py \
  --output .test-artifacts/benchmarks/context-benchmark.json
```

The JSON records exact tokenizer counts, output-to-input ratios, selected and omitted blocks, phrase retention, lexical query-term coverage, elapsed time and Python-traced peak memory. It also compares the cleaner on inputs containing 1,000, 2,000 and 4,000 blank lines, and checks focused retrieval for evidence near the beginning, middle and end of a short source under a tight budget.

Phrase and query-term coverage are evidence proxies, not model answer accuracy. The scale fixture combines fixed evidence statements with synthetic routine records. A small source can grow after source labels and provenance are added; a budget-limited export can omit evidence. Ratios therefore apply to the measured fixture and configuration, not to every document.

When enabled, LLMLingua-2 is tried on eligible prose blocks before the selector chooses evidence. Numeric and qualifier-bearing prose, code, tables and other protected structures are excluded from compression. The export still counts the final selected content and can fall back to original text if compression increases its size. It does not call a paid service or download weights. Compression can change wording and must be reviewed against the source before relying on it. The benchmark does not establish that an LLM answers questions more accurately.
