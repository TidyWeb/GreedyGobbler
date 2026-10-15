"""Offline source-context benchmark with exact, reproducible local fixtures.

Retention and query coverage are lexical proxies. This script does not claim
LLM answer accuracy and never calls a provider or downloads a model.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import tracemalloc
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from pipeline.context import prepare_context
from pipeline.token_budget import make_counter

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "fixtures"
WORD_RE = re.compile(r"[^\W_]+(?:[.-][^\W_]+)*", re.UNICODE)


def _extraction(text: str, *, name: str, uri: str) -> dict[str, Any]:
    return {
        "raw": text,
        "cleaned": text,
        "sources": [{"id": "S1", "name": name, "uri": uri}],
        "units": [
            {
                "id": "S1-U0001",
                "source_id": "S1",
                "locator": "whole fixture",
                "raw": text,
                "text": text,
            }
        ],
        "warnings": [],
        "omissions": [],
        "metrics": {},
    }


def _measure(
    label: str,
    extraction: dict[str, Any],
    options: dict[str, Any],
    facts: list[dict[str, str]],
) -> dict[str, Any]:
    tracemalloc.start()
    started = time.perf_counter()
    result = prepare_context(extraction, options)
    runtime = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    content = result["content"]
    retained = [fact["id"] for fact in facts if fact["phrase"] in content]
    query_terms = {
        term.casefold()
        for fact in facts
        for term in WORD_RE.findall(fact["query"])
        if len(term) > 2
    }
    output_terms = {term.casefold() for term in WORD_RE.findall(content)}
    query_covered = query_terms & output_terms
    stats = result["stats"]
    return {
        "label": label,
        "mode": options.get("mode", "faithful"),
        "compression": options.get("compression", "none"),
        "stats": stats,
        "selected_blocks": len(result["selected"]),
        "omitted_blocks": len(result["omitted"]),
        "retained_fact_ids": retained,
        "fact_retention_ratio": round(len(retained) / max(1, len(facts)), 4),
        "query_term_coverage_proxy": {
            "covered": len(query_covered),
            "total": len(query_terms),
            "ratio": round(len(query_covered) / max(1, len(query_terms)), 4),
            "label": "lexical coverage proxy; not LLM answer accuracy",
        },
        "runtime_seconds": round(runtime, 6),
        "tracemalloc_peak_bytes": peak,
        "export_bytes": len(content.encode("utf-8")),
    }


def _large_case(block_count: int, budget: int) -> dict[str, Any]:
    text = "\n\n".join(
        f"Record {index:04d} states the ordinary observation for station {index % 17}."
        for index in range(block_count)
    )
    extraction = _extraction(
        text, name=f"Synthetic scale fixture with {block_count} blocks", uri="scale.md"
    )
    measured = _measure(
        f"scale_{block_count}",
        extraction,
        {"tokenizer": "cl100k_base", "mode": "faithful", "max_tokens": budget},
        [],
    )
    measured["input_blocks"] = block_count
    measured["budget_tokens"] = budget
    measured["selection_fraction"] = round(
        measured["selected_blocks"] / max(1, block_count), 4
    )
    return measured


def run_benchmark(compressor_model: str | None = None) -> dict[str, Any]:
    raw = (FIXTURES / "context_sample.md").read_text(encoding="utf-8")
    cleaned = (FIXTURES / "context_cleaned.md").read_text(encoding="utf-8")
    facts_doc = json.loads((FIXTURES / "context_facts.json").read_text(encoding="utf-8"))
    facts = facts_doc["facts"]
    tokenizer = "cl100k_base"
    # Keep first tokenizer initialization outside mode timings; the benchmark
    # then measures context processing rather than cache/module startup.
    make_counter(tokenizer).count("warm local tokenizer")
    budget = 900

    cases = [
        _measure(
            "raw_faithful",
            _extraction(raw, name="Raw field note", uri="context_sample.md"),
            {"tokenizer": tokenizer, "mode": "faithful", "max_tokens": budget},
            facts,
        ),
        _measure(
            "conservative_clean_faithful",
            _extraction(cleaned, name="Cleaned field note", uri="context_cleaned.md"),
            {"tokenizer": tokenizer, "mode": "faithful", "max_tokens": budget},
            facts,
        ),
        _measure(
            "clean_balanced",
            _extraction(cleaned, name="Cleaned field note", uri="context_cleaned.md"),
            {"tokenizer": tokenizer, "mode": "balanced", "max_tokens": budget},
            facts,
        ),
        _measure(
            "clean_focused",
            _extraction(cleaned, name="Cleaned field note", uri="context_cleaned.md"),
            {
                "tokenizer": tokenizer,
                "mode": "focused",
                "query": facts_doc["focused_query"],
                "max_tokens": budget,
            },
            facts,
        ),
    ]
    if compressor_model:
        cases.append(
            _measure(
                "clean_llmlingua2_experimental",
                _extraction(cleaned, name="Cleaned field note", uri="context_cleaned.md"),
                {
                    "tokenizer": tokenizer,
                    "mode": "faithful",
                    "compression": "llmlingua2",
                    "compression_rate": 0.5,
                    "compressor_model": compressor_model,
                    "max_tokens": budget,
                },
                facts,
            )
        )

    scales = [_large_case(block_count, 12000) for block_count in (1000, 2000, 4000)]
    return {
        "benchmark": "greedygobbler-source-context",
        "schema_version": 1,
        "tokenizer": tokenizer,
        "count_kind": "exact_tokens",
        "fixture": "benchmarks/fixtures/context_sample.md",
        "metrics_note": (
            "Exact phrase retention and lexical query-term coverage are offline proxies, "
            "not generated-answer accuracy. Runtime and tracemalloc are local measurements."
        ),
        "cases": cases,
        "scaling": scales,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="JSON output file to create")
    parser.add_argument(
        "--compressor-model",
        help="Optional existing local LLMLingua-2 model directory; never downloaded",
    )
    args = parser.parse_args()
    report = run_benchmark(args.compressor_model)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "cases": len(report["cases"]), "scales": len(report["scaling"])}, indent=2))


if __name__ == "__main__":
    main()
