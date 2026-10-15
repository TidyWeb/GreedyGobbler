"""Token counting with explicit exact-tokenizer and byte-bound modes."""

from __future__ import annotations

import hashlib
import importlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_ENCODING_FILES = {
    "cl100k_base": (
        "https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken",
        "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7",
    ),
    "o200k_base": (
        "https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken",
        "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d",
    ),
}


@dataclass
class TokenCounter:
    tokenizer: str
    count_kind: str
    _encoding: Any = None

    def count(self, text: str) -> int:
        if not isinstance(text, str):
            raise TypeError("Token counting requires text.")
        if self.tokenizer == "utf8_bytes":
            return len(text.encode("utf-8"))
        if self._encoding is None:
            self._encoding = _load_exact_encoding(self.tokenizer)
        return len(self._encoding.encode(text, disallowed_special=()))


def make_counter(tokenizer: str) -> TokenCounter:
    if tokenizer not in {"cl100k_base", "o200k_base", "utf8_bytes"}:
        raise ValueError(
            "tokenizer must be cl100k_base, o200k_base, or utf8_bytes."
        )
    kind = "utf8_bytes_bound" if tokenizer == "utf8_bytes" else "exact_tokens"
    return TokenCounter(tokenizer=tokenizer, count_kind=kind)


def _cache_file(url: str) -> Path:
    if "TIKTOKEN_CACHE_DIR" in os.environ:
        cache_dir = os.environ["TIKTOKEN_CACHE_DIR"]
    elif "DATA_GYM_CACHE_DIR" in os.environ:
        cache_dir = os.environ["DATA_GYM_CACHE_DIR"]
    else:
        cache_dir = str(Path(tempfile.gettempdir()) / "data-gym-cache")
    return Path(cache_dir) / hashlib.sha1(url.encode("utf-8")).hexdigest()


def _load_exact_encoding(name: str) -> Any:
    """Load only a verified local tiktoken cache entry; never fetch or clean it."""
    if name not in _ENCODING_FILES:
        raise ValueError(f"Unsupported exact tokenizer: {name}.")
    url, expected_hash = _ENCODING_FILES[name]
    path = _cache_file(url)
    if not path.is_file():
        raise ValueError(
            f"Exact tokenizer {name} is not cached locally. Install tiktoken and "
            "populate its cache explicitly, or choose tokenizer='utf8_bytes' "
            "for a conservative UTF-8 byte bound."
        )
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"Cannot read the local {name} tokenizer cache: {exc}") from exc
    actual_hash = hashlib.sha256(data).hexdigest()
    if actual_hash != expected_hash:
        raise ValueError(
            f"The local {name} tokenizer cache failed its integrity check. "
            "Restore a verified tiktoken cache entry or choose utf8_bytes."
        )
    try:
        # tiktoken.get_encoding may download missing files and may remove a bad
        # cache entry. Preflight the exact cache bytes before asking it to load.
        tiktoken = importlib.import_module("tiktoken")
        return tiktoken.get_encoding(name)
    except Exception as exc:
        raise ValueError(
            f"Could not load exact tokenizer {name} from the verified local cache. "
            "Check the tiktoken installation or choose utf8_bytes."
        ) from exc
