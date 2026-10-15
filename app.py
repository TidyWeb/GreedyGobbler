"""Local Flask interface for document extraction and context preparation."""

from __future__ import annotations

import importlib
import json
import os
import re
import threading
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.utils import secure_filename


DEFAULT_OPTIONS = {
    "depth": 1,
    "profile": "document",
    "trim_body": False,
    "allow_remote": False,
    "use_browser": False,
    "max_tokens": 8000,
    "tokenizer": "cl100k_base",
    "mode": "faithful",
    "query": "",
    "context_window": None,
    "reserved_tokens": 0,
    "answer_tokens": 0,
    "compression": "none",
    "compression_rate": 0.5,
}

_OPTION_KEYS = frozenset(DEFAULT_OPTIONS)
_TOP_LEVEL_KEYS = frozenset({"source", "is_url", "depth", "options"})


class RequestValidationError(ValueError):
    """Raised when a client request does not match the supported API shape."""


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise RuntimeError(f"{name} must be an integer") from None
    if parsed <= 0:
        raise RuntimeError(f"{name} must be greater than zero")
    return parsed


def _default_data_root() -> Path:
    configured = os.environ.get("XDG_DATA_HOME")
    if configured:
        return Path(configured).expanduser() / "greedygobbler"
    return Path.home() / ".local" / "share" / "greedygobbler"


def _default_tokenizer_cache() -> Path:
    configured = os.environ.get("XDG_CACHE_HOME")
    cache_root = Path(configured).expanduser() if configured else Path.home() / ".cache"
    return cache_root / "greedygobbler" / "tokenizers"


def _json_error(message: str, status: int):
    return jsonify({"status": "error", "message": message}), status


def _is_integer(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_options(provided) -> dict:
    if not isinstance(provided, dict):
        raise RequestValidationError("Options must be a JSON object.")

    unknown = sorted(set(provided) - _OPTION_KEYS)
    if unknown:
        raise RequestValidationError(
            "Unsupported option: " + ", ".join(unknown) + "."
        )

    options = {**DEFAULT_OPTIONS, **provided}
    if not _is_integer(options["depth"]) or options["depth"] not in (1, 2):
        raise RequestValidationError("Depth must be the integer 1 or 2.")
    if options["profile"] not in ("document", "web", "reddit"):
        raise RequestValidationError("Profile must be document, web, or reddit.")
    for key in ("trim_body", "allow_remote", "use_browser"):
        if not isinstance(options[key], bool):
            raise RequestValidationError(f"{key} must be true or false.")
    if not _is_integer(options["max_tokens"]) or options["max_tokens"] <= 0:
        raise RequestValidationError("max_tokens must be a positive integer.")
    if options["tokenizer"] not in ("cl100k_base", "o200k_base", "utf8_bytes"):
        raise RequestValidationError(
            "tokenizer must be cl100k_base, o200k_base, or utf8_bytes."
        )
    if options["mode"] not in ("faithful", "balanced", "focused"):
        raise RequestValidationError("mode must be faithful, balanced, or focused.")
    if not isinstance(options["query"], str):
        raise RequestValidationError("query must be text.")
    for key in ("context_window",):
        value = options[key]
        if value is not None and (not _is_integer(value) or value <= 0):
            raise RequestValidationError(f"{key} must be a positive integer or null.")
    for key in ("reserved_tokens", "answer_tokens"):
        if not _is_integer(options[key]) or options[key] < 0:
            raise RequestValidationError(f"{key} must be a nonnegative integer.")
    if options["compression"] not in ("none", "llmlingua2"):
        raise RequestValidationError("compression must be none or llmlingua2.")
    rate = options["compression_rate"]
    if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not 0 < rate <= 1:
        raise RequestValidationError("compression_rate must be greater than 0 and at most 1.")
    options["compression_rate"] = float(rate)
    return options


def _validate_source(value, *, is_url: bool) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RequestValidationError("Provide a non-empty source.")
    source = value.strip()
    if is_url:
        parsed = urlsplit(source)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise RequestValidationError("Website source must be an http or https URL.")
        if parsed.username or parsed.password:
            raise RequestValidationError("Website URLs must not contain embedded credentials.")
    return source


def _same_origin() -> bool:
    origin = request.headers.get("Origin")
    if not origin:
        return True
    try:
        incoming = urlsplit(origin)
        current = urlsplit(request.host_url)
        if incoming.scheme not in ("http", "https") or not incoming.hostname:
            return False
        if incoming.username or incoming.password or incoming.path not in ("", "/"):
            return False
        incoming_port = incoming.port or (443 if incoming.scheme == "https" else 80)
        current_port = current.port or (443 if current.scheme == "https" else 80)
        return (
            incoming.scheme.lower() == current.scheme.lower()
            and incoming.hostname.lower() == (current.hostname or "").lower()
            and incoming_port == current_port
        )
    except ValueError:
        return False


def _request_json_object() -> dict:
    if not request.is_json:
        raise RequestValidationError("Send a JSON request body.")
    try:
        data = request.get_json(silent=True)
    except (TypeError, ValueError):
        raise RequestValidationError("Request body is not valid JSON.") from None
    if data is None:
        raise RequestValidationError("Request body is not valid JSON.")
    if not isinstance(data, dict):
        raise RequestValidationError("Request body must be a JSON object.")
    return data


def _parse_json_process_request(data: dict) -> tuple[str, bool, dict]:
    unknown = sorted(set(data) - _TOP_LEVEL_KEYS)
    if unknown:
        raise RequestValidationError("Unsupported request field: " + ", ".join(unknown) + ".")
    is_url = data.get("is_url", True)
    if not isinstance(is_url, bool):
        raise RequestValidationError("is_url must be true or false.")
    source = _validate_source(data.get("source"), is_url=is_url)
    if not is_url:
        raise RequestValidationError("Local files must be sent as a multipart file upload.")

    supplied = data.get("options", {})
    if not isinstance(supplied, dict):
        raise RequestValidationError("options must be a JSON object.")
    # Keep the former top-level depth option working for existing clients.
    if "depth" in data:
        if "depth" in supplied:
            raise RequestValidationError("Send depth either in options or at the top level, not both.")
        supplied = {**supplied, "depth": data["depth"]}
    return source, True, _validate_options(supplied)


def _parse_multipart_process_request() -> tuple[str, bool, dict, object]:
    unexpected_form_fields = set(request.form) - (_OPTION_KEYS | {"options"})
    if unexpected_form_fields:
        raise RequestValidationError(
            "Unsupported multipart field: " + ", ".join(sorted(unexpected_form_fields)) + "."
        )
    unexpected_files = set(request.files) - {"file"}
    if unexpected_files or len(request.files.getlist("file")) > 1:
        raise RequestValidationError("Send one file upload per request.")
    upload = request.files.get("file")
    if upload is None:
        raise RequestValidationError("Choose a file to upload.")
    original_name = upload.filename
    if not isinstance(original_name, str) or not original_name.strip():
        raise RequestValidationError("Choose a file with a filename.")
    safe_name = secure_filename(Path(original_name.replace("\\", "/")).name)
    if safe_name in ("", ".", ".."):
        raise RequestValidationError("Choose a file with a usable filename.")

    raw_options = request.form.get("options", "{}")
    try:
        supplied = json.loads(raw_options)
    except (TypeError, ValueError):
        raise RequestValidationError("Multipart options must contain valid JSON.") from None
    if not isinstance(supplied, dict):
        raise RequestValidationError("Multipart options must be a JSON object.")
    for key in _OPTION_KEYS:
        if key in request.form:
            if key in supplied:
                raise RequestValidationError(f"Send {key} either in options or as a form field, not both.")
            supplied[key] = request.form.get(key)
    # HTML form scalar values are strings; accept only explicit canonical values.
    for key in ("trim_body", "allow_remote", "use_browser"):
        if key in supplied and isinstance(supplied[key], str):
            if supplied[key] not in ("true", "false"):
                raise RequestValidationError(f"{key} must be true or false.")
            supplied[key] = supplied[key] == "true"
    for key in ("depth", "max_tokens", "context_window", "reserved_tokens", "answer_tokens"):
        value = supplied.get(key)
        if isinstance(value, str):
            if value == "" and key == "context_window":
                supplied[key] = None
            else:
                try:
                    supplied[key] = int(value)
                except ValueError:
                    raise RequestValidationError(f"{key} must be an integer.") from None
    if "compression_rate" in supplied and isinstance(supplied["compression_rate"], str):
        try:
            supplied["compression_rate"] = float(supplied["compression_rate"])
        except ValueError:
            raise RequestValidationError("compression_rate must be a number.") from None
    return safe_name, False, _validate_options(supplied), upload


def _update_progress(processing_state: dict, job_id: str, values: dict) -> None:
    if not isinstance(values, dict):
        return
    done = values.get("pages_done", 0)
    queued = values.get("pages_queued", 0)
    if not _is_integer(done) or not _is_integer(queued):
        return
    with processing_state["state_lock"]:
        progress = processing_state["progress"]
        if progress["job_id"] == job_id:
            progress.update(pages_done=max(0, done), pages_queued=max(0, queued))


def _load_processing_services(app: Flask):
    extractor = app.config.get("EXTRACTOR")
    if extractor is None:
        try:
            extractor = getattr(importlib.import_module("converter"), "extract_input")
        except (ImportError, AttributeError) as exc:
            raise RuntimeError("The document extractor is unavailable.") from exc

    context_preparer = app.config.get("CONTEXT_PREPARER")
    if context_preparer is None:
        try:
            context_preparer = getattr(
                importlib.import_module("pipeline.context"), "prepare_context"
            )
        except (ImportError, AttributeError) as exc:
            raise RuntimeError("The context preparation module is unavailable.") from exc
    return extractor, context_preparer


def _store_upload(app: Flask, upload, original_name: str) -> str:
    temporary_dir = Path(app.config["TEMP_DIR"]).expanduser()
    temporary_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(original_name).suffix.lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,12}", suffix):
        suffix = ".upload"
    target = temporary_dir / f"upload-{uuid.uuid4().hex}{suffix}"
    # UUID naming and exclusive creation make same-name uploads independent.
    with target.open("xb") as destination:
        upload.save(destination)
    return str(target)


def _validate_extraction(extraction) -> dict:
    if not isinstance(extraction, dict):
        raise ValueError("The extractor returned an invalid result.")
    for key in ("raw", "cleaned"):
        if not isinstance(extraction.get(key), str):
            raise ValueError("The extractor returned an invalid result.")
    if not extraction["raw"].strip() and not extraction["cleaned"].strip():
        raise ValueError("No readable content was extracted from this source.")
    if not isinstance(extraction.get("sources", []), list):
        raise ValueError("The extractor returned an invalid source list.")
    if not isinstance(extraction.get("units", []), list) or any(
        not isinstance(unit, dict) for unit in extraction.get("units", [])
    ):
        raise ValueError("The extractor returned an invalid unit list.")
    return extraction


def _safe_processing_error(message: str, *, source: str, source_name: str, is_url: bool, model_path) -> str:
    safe_message = message.strip() or "The source could not be processed."
    if not is_url:
        safe_message = safe_message.replace(source, source_name)
    if model_path:
        safe_message = safe_message.replace(os.fspath(model_path), "configured local model")
    if re.search(r"(?<!https:)(?<!http:)/(?:home|tmp|data|var|root|opt|srv|app)/", safe_message):
        return "The source could not be processed. Check the file format and local dependencies."
    return safe_message


def _count_text(text: str, tokenizer: str) -> tuple[int, str]:
    if tokenizer == "utf8_bytes":
        return len(text.encode("utf-8")), "utf8_bytes"
    try:
        make_counter = importlib.import_module("pipeline.token_budget").make_counter
        counter = make_counter(tokenizer)
        count = counter.count(text)
        if not _is_integer(count):
            raise ValueError("The token counter returned an invalid count.")
        return count, "tokens"
    except Exception as exc:
        raise RequestValidationError(
            f"Exact counting with {tokenizer} is unavailable. Install its tokenizer data locally before using this counter."
        ) from exc


def create_app(config: dict | None = None) -> Flask:
    """Create a separately configurable local app without writing to disk."""
    app = Flask(__name__, static_folder="static")
    data_root = _default_data_root()
    app.config.from_mapping(
        SAVE_DIR=os.environ.get(
            "SAVE_DIR", str(Path.home() / "Downloads" / "GreedyGobbler")
        ),
        TEMP_DIR=os.environ.get("TEMP_DIR", str(data_root / "uploads")),
        MAX_CONTENT_LENGTH=_env_int("MAX_CONTENT_LENGTH", 100 * 1024 * 1024),
        COMPRESSOR_MODEL=os.environ.get("COMPRESSOR_MODEL"),
        TIKTOKEN_CACHE_DIR=os.environ.get("TIKTOKEN_CACHE_DIR", str(_default_tokenizer_cache())),
        EXTRACTOR=None,
        CONTEXT_PREPARER=None,
        TESTING=False,
    )
    if config:
        app.config.update(config)
    os.environ.setdefault("TIKTOKEN_CACHE_DIR", os.fspath(app.config["TIKTOKEN_CACHE_DIR"]))
    processing_state = {
        "job_lock": threading.Lock(),
        "state_lock": threading.Lock(),
        "progress": {
            "status": "idle",
            "pages_done": 0,
            "pages_queued": 0,
            "message": "Ready to process.",
            "job_id": None,
        },
    }
    app.extensions["processing_state"] = processing_state

    @app.errorhandler(RequestEntityTooLarge)
    def request_too_large(_error):
        return _json_error("Request exceeds the configured upload size limit.", 413)

    @app.get("/")
    def index():
        return app.send_static_file("index.html")

    @app.post("/process")
    def process():
        if not _same_origin():
            return _json_error("Cross-origin requests are not accepted.", 403)

        try:
            if request.mimetype == "multipart/form-data":
                source_name, is_url, options, upload = _parse_multipart_process_request()
            elif request.is_json:
                source_name, is_url, options = _parse_json_process_request(_request_json_object())
                upload = None
            else:
                raise RequestValidationError("Send a JSON URL request or a multipart file upload.")
        except RequestValidationError as exc:
            return _json_error(str(exc), 400)

        job_lock = processing_state["job_lock"]
        state_lock = processing_state["state_lock"]
        progress_state = processing_state["progress"]
        if not job_lock.acquire(blocking=False):
            return _json_error("Another source is being processed. Try again when it finishes.", 409)

        job_id = uuid.uuid4().hex
        with state_lock:
            progress_state.update(
                status="processing",
                pages_done=0,
                pages_queued=0,
                message="Processing source.",
                job_id=job_id,
            )
        try:
            source = source_name
            if upload is not None:
                try:
                    source = _store_upload(app, upload, source_name)
                except OSError:
                    with state_lock:
                        progress_state.update(status="error", message="The upload could not be stored.")
                    return _json_error("The upload could not be stored.", 500)

            try:
                extractor, prepare_context = _load_processing_services(app)
                extraction = extractor(
                    source,
                    is_url=is_url,
                    depth=options["depth"],
                    profile=options["profile"],
                    trim_body=options["trim_body"],
                    allow_remote=options["allow_remote"],
                    use_browser=options["use_browser"],
                    progress=lambda values: _update_progress(processing_state, job_id, values),
                )
                extraction = _validate_extraction(extraction)
                if upload is not None:
                    for source_record in extraction.get("sources", []):
                        if isinstance(source_record, dict):
                            source_record["name"] = source_name
                            source_record["uri"] = source_name
                pack_options = {
                    key: options[key]
                    for key in (
                        "max_tokens",
                        "tokenizer",
                        "mode",
                        "query",
                        "context_window",
                        "reserved_tokens",
                        "answer_tokens",
                        "compression",
                        "compression_rate",
                    )
                }
                model_path = app.config.get("COMPRESSOR_MODEL")
                if model_path:
                    pack_options["compressor_model"] = os.fspath(model_path)
                packed = prepare_context(extraction, pack_options)
                if not isinstance(packed, dict) or not isinstance(packed.get("content"), str):
                    raise ValueError("The context preparation module returned an invalid result.")
                if not packed["content"].strip():
                    raise ValueError("No context could be prepared from this source.")
            except RequestValidationError as exc:
                with state_lock:
                    progress_state.update(status="error", message=str(exc))
                return _json_error(str(exc), 400)
            except ValueError as exc:
                message = _safe_processing_error(
                    str(exc),
                    source=source,
                    source_name=source_name,
                    is_url=is_url,
                    model_path=app.config.get("COMPRESSOR_MODEL"),
                )
                with state_lock:
                    progress_state.update(status="error", message=message)
                return _json_error(message, 400)
            except Exception:
                with state_lock:
                    progress_state.update(status="error", message="Processing failed. Check the selected source and local dependencies.")
                return _json_error("Processing failed. Check the selected source and local dependencies.", 500)

            warnings = extraction.get("warnings", [])
            if not isinstance(warnings, list):
                warnings = []
            pack_warnings = packed.get("warnings", [])
            if isinstance(pack_warnings, list):
                warnings = warnings + pack_warnings
            response = {
                "status": "success",
                "raw": extraction["raw"],
                "cleaned": extraction["cleaned"],
                "content": packed["content"],
                "extraction": {
                    "sources": extraction.get("sources", []),
                    "units": [
                        {
                            key: unit[key]
                            for key in ("id", "source_id", "locator")
                            if key in unit
                        }
                        for unit in extraction.get("units", [])
                    ],
                    "warnings": extraction.get("warnings", []),
                    "omissions": extraction.get("omissions", []),
                    "metrics": extraction.get("metrics", {}),
                },
                "stats": packed.get("stats", {}),
                "sources": packed.get("sources", extraction.get("sources", [])),
                "selected": packed.get("selected", []),
                "omitted": packed.get("omitted", extraction.get("omissions", [])),
                "warnings": warnings,
                "outline": packed.get("outline", []),
            }
            with state_lock:
                progress_state.update(status="finished", message="Processing complete.")
            return jsonify(response)
        finally:
            job_lock.release()

    @app.post("/count")
    def count():
        if not _same_origin():
            return _json_error("Cross-origin requests are not accepted.", 403)
        try:
            data = _request_json_object()
            if set(data) != {"text", "tokenizer"}:
                raise RequestValidationError("Send text and tokenizer fields only.")
            text = data["text"]
            tokenizer = data["tokenizer"]
            if not isinstance(text, str):
                raise RequestValidationError("text must be a string.")
            if tokenizer not in ("cl100k_base", "o200k_base", "utf8_bytes"):
                raise RequestValidationError("Choose a supported tokenizer.")
            count_value, count_kind = _count_text(text, tokenizer)
            return jsonify({"status": "success", "count": count_value, "count_kind": count_kind, "tokenizer": tokenizer})
        except RequestValidationError as exc:
            return _json_error(str(exc), 400)

    @app.post("/download")
    def download():
        if not _same_origin():
            return _json_error("Cross-origin requests are not accepted.", 403)
        try:
            data = _request_json_object()
            if set(data) != {"filename", "content"}:
                raise RequestValidationError("Send filename and content fields only.")
            filename = data["filename"]
            content = data["content"]
            if not isinstance(filename, str) or not filename or len(filename) > 180:
                raise RequestValidationError("Filename must be a simple .md basename.")
            if (
                filename in (".", "..")
                or filename.startswith(".")
                or "/" in filename
                or "\\" in filename
                or Path(filename).name != filename
                or not filename.lower().endswith(".md")
                or any(ord(character) < 32 or ord(character) == 127 for character in filename)
            ):
                raise RequestValidationError("Filename must be a simple .md basename.")
            if not isinstance(content, str):
                raise RequestValidationError("content must be a string.")
        except RequestValidationError as exc:
            return _json_error(str(exc), 400)

        try:
            configured_dir = Path(app.config["SAVE_DIR"]).expanduser()
            configured_dir.mkdir(parents=True, exist_ok=True)
            save_root = configured_dir.resolve(strict=True)
        except OSError:
            return _json_error("The configured save folder is unavailable.", 500)

        requested = Path(filename)
        stem = requested.stem
        suffix = requested.suffix
        for number in range(1, 10000):
            candidate_name = filename if number == 1 else f"{stem}-{number}{suffix}"
            candidate = save_root / candidate_name
            try:
                if candidate.is_symlink():
                    return _json_error("The requested save target is a symbolic link.", 400)
                resolved = candidate.resolve(strict=False)
                if resolved.parent != save_root:
                    return _json_error("Save target must remain inside the configured save folder.", 400)
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                if hasattr(os, "O_NOFOLLOW"):
                    flags |= os.O_NOFOLLOW
                file_descriptor = os.open(candidate, flags, 0o600)
            except FileExistsError:
                continue
            except OSError:
                return _json_error("The file could not be saved safely.", 500)
            try:
                with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="") as destination:
                    destination.write(content)
            except OSError:
                return _json_error("The file could not be saved.", 500)
            return jsonify({"status": "success", "path": str(candidate), "filename": candidate_name})
        return _json_error("A unique filename could not be allocated.", 409)

    @app.get("/progress")
    def progress():
        with processing_state["state_lock"]:
            return jsonify(dict(processing_state["progress"]))

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=False, host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "5001")))
