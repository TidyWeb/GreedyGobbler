"""Optional, explicitly selected local LLMLingua-2 prose compression."""

from __future__ import annotations

import importlib
import re
import threading
from collections.abc import Callable, Iterable
from pathlib import Path

from .context_models import ContextBlock
from .token_budget import make_counter

_BACKEND_LOCK = threading.RLock()
_BACKEND_CACHE_PATH: str | None = None
_BACKEND_CACHE: Callable[[str, float], str] | None = None
_PROTECTED_RE = re.compile(
    r"\d|[\"“”‘’]|"
    r"\b(?:not|never|no|none|neither|nor|except|unless|only|must|may|"
    r"cannot|can't|won't|shouldn't|doesn't|didn't|isn't|aren't|wasn't|"
    r"weren't|hasn't|haven't|hadn't|without|at\s+least|at\s+most|"
    r"less\s+than|more\s+than|up\s+to|however|although|but|subject\s+to)\b",
    re.IGNORECASE,
)


def _is_eligible_prose(block: ContextBlock) -> bool:
    return block.kind == "paragraph" and not _PROTECTED_RE.search(block.text)


def _load_llmlingua_backend(model_path: str) -> Callable[[str, float], str]:
    local_model = Path(model_path).expanduser()
    if not local_model.is_dir():
        raise ValueError(
            "LLMLingua-2 requires compressor_model to name an existing local "
            "model directory. Model downloads are disabled."
        )
    cache_key = str(local_model.resolve())
    global _BACKEND_CACHE_PATH, _BACKEND_CACHE
    with _BACKEND_LOCK:
        if _BACKEND_CACHE is not None and _BACKEND_CACHE_PATH == cache_key:
            return _BACKEND_CACHE
        engine = _initialize_llmlingua(local_model)
        _BACKEND_CACHE_PATH = cache_key
        _BACKEND_CACHE = engine
        return engine


def _initialize_llmlingua(local_model: Path) -> Callable[[str, float], str]:
    _ensure_llmlingua_tokenizer()
    try:
        package = importlib.import_module("llmlingua")
        compressor_class = package.PromptCompressor
    except Exception as exc:
        raise ValueError(
            "LLMLingua-2 is unavailable. Install the optional llmlingua and "
            "transformers packages, then select an existing local model."
        ) from exc

    try:
        compressor = compressor_class(
            model_name=str(local_model),
            use_llmlingua2=True,
            device_map="cpu",
            model_config={
                "local_files_only": True,
                "trust_remote_code": False,
            },
        )
        # LLMLingua 0.2.2 detects BERT token boundaries by searching
        # ``self.model_name`` for the upstream architecture label. When loaded
        # from an arbitrary local directory that label is lost, so restore only
        # the family marker after the local model has been loaded and inspected.
        model_config = getattr(getattr(compressor, "model", None), "config", None)
        model_type = getattr(model_config, "model_type", None)
        family_name = {
            "bert": "bert-base-multilingual-cased",
            "xlm-roberta": "xlm-roberta-large",
        }.get(model_type)
        if model_config is not None and family_name is None:
            raise ValueError(
                f"Unsupported LLMLingua-2 tokenizer family: {model_type!r}."
            )
        if family_name is not None:
            compressor.model_name = family_name
    except Exception as exc:
        raise ValueError(
            "Could not load the configured local LLMLingua-2 model. Verify that "
            "its files and optional runtime dependencies are present. "
            f"Details: {exc}"
        ) from exc

    def compress(text: str, rate: float) -> str:
        try:
            result = compressor.compress_prompt(text, rate=rate)
        except Exception as exc:
            raise ValueError(f"LLMLingua-2 compression failed: {exc}") from exc
        if not isinstance(result, dict) or not isinstance(
            result.get("compressed_prompt"), str
        ):
            raise ValueError("LLMLingua-2 returned an invalid compression result.")
        return result["compressed_prompt"]

    return compress


def _ensure_llmlingua_tokenizer() -> None:
    """Preflight LLMLingua's own cl100k dependency without network fallback."""
    try:
        make_counter("cl100k_base").count("")
    except ValueError as exc:
        raise ValueError(
            "LLMLingua-2 requires the verified cl100k_base tokenizer cache used "
            "by its upstream compressor. Populate the local cache explicitly."
        ) from exc


def compress_prose_blocks(
    blocks: Iterable[ContextBlock],
    *,
    model_path: str | None,
    rate: float,
    count_tokens: Callable[[str], int],
    backend: Callable[[str, float], str] | None = None,
) -> tuple[dict[str, str], list[str]]:
    """Compress only low-risk prose and report conservative skips or failures."""
    if not model_path:
        raise ValueError(
            "compression='llmlingua2' requires a server-configured local "
            "compressor_model path."
        )
    materialized = list(blocks)
    engine = backend or _load_llmlingua_backend(model_path)
    replacements: dict[str, str] = {}
    warnings: list[str] = [
        "LLMLingua-2 compression is experimental and lossy; review the original source."
    ]
    skipped = 0
    inflated = 0
    for block in materialized:
        if not _is_eligible_prose(block):
            skipped += 1
            continue
        try:
            compressed = engine(block.text, rate)
        except Exception as exc:
            raise ValueError(f"LLMLingua-2 compression failed: {exc}") from exc
        if not isinstance(compressed, str) or not compressed.strip():
            raise ValueError("The compressor returned empty or non-text prose.")
        if count_tokens(compressed) > count_tokens(block.text):
            inflated += 1
            continue
        if compressed != block.text:
            replacements[block.block_id] = compressed
    if skipped:
        warnings.append(
            f"Skipped compression for {skipped} protected or non-prose block(s)."
        )
    if inflated:
        warnings.append(
            f"Kept original text for {inflated} block(s) whose compression inflated."
        )
    return replacements, warnings
