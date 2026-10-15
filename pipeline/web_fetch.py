"""Bounded, local-first HTTP extraction with same-host breadth-first crawling."""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from urllib.parse import urldefrag, urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

from pipeline.extraction import ConversionError, _epub_children
from pipeline.normaliser import clean


MAX_PAGES = 25
MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 5
HTTP_TIMEOUT = (5, 20)
POLITE_DELAY_SECONDS = 0.1

_host_locks: dict[str, threading.Lock] = {}
_host_last_request: dict[str, float] = {}
_host_guard = threading.Lock()


@dataclass
class HttpPage:
    url: str
    body: bytes
    content_type: str
    status_code: int


def normalize_url(url: str) -> str:
    if not isinstance(url, str) or not url.strip():
        raise ConversionError("URL must be a non-empty HTTP or HTTPS address")
    try:
        parsed = urlsplit(url.strip())
        _ = parsed.port
    except ValueError as exc:
        raise ConversionError("URL contains an invalid port") from exc
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise ConversionError("Only HTTP and HTTPS URLs can be extracted")
    if parsed.username is not None or parsed.password is not None:
        raise ConversionError("URLs containing usernames or passwords are not allowed")
    if not parsed.hostname:
        raise ConversionError("URL must include a hostname")
    normalized = urlunsplit((scheme, parsed.netloc.lower(), parsed.path or "/", parsed.query, ""))
    return urldefrag(normalized)[0]


def _host_name(url: str) -> str:
    return (urlsplit(url).hostname or "").rstrip(".").lower()


def _resolve_public_addresses(host: str, port: int) -> list[str]:
    try:
        direct = ipaddress.ip_address(host)
    except ValueError:
        try:
            return [record[4][0] for record in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)]
        except OSError as exc:
            raise ConversionError(f"Could not resolve host '{host}': {exc}") from exc
    return [str(direct)]


def validate_public_url(url: str, resolver=None) -> str:
    normalized = normalize_url(url)
    parsed = urlsplit(normalized)
    host = (parsed.hostname or "").rstrip(".").lower()
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost") or host.endswith(".local"):
        raise ConversionError("Local and .local network addresses are not allowed")
    address_resolver = resolver or _resolve_public_addresses
    addresses = address_resolver(host, port)
    if not addresses:
        raise ConversionError(f"Host '{host}' did not resolve to an address")
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise ConversionError(f"Host '{host}' resolved to an invalid address") from exc
        if not ip.is_global:
            raise ConversionError(f"Local, private, reserved, and link-local addresses are not allowed ({ip})")
    return normalized


def _polite_lock(host: str):
    with _host_guard:
        return _host_locks.setdefault(host, threading.Lock())


def _read_response_body(response, limit: int) -> bytes:
    chunks = []
    total = 0
    iterator = response.iter_content(chunk_size=64 * 1024)
    for chunk in iterator:
        if not chunk:
            continue
        total += len(chunk)
        if total > limit:
            raise ConversionError(f"Page exceeded the {limit // (1024 * 1024)} MiB response limit")
        chunks.append(chunk)
    return b"".join(chunks)


def fetch_page(
    url: str,
    *,
    expected_host: str,
    resolver=None,
    request_get=None,
    max_body_bytes: int = MAX_BODY_BYTES,
    timeout=HTTP_TIMEOUT,
) -> HttpPage:
    """Fetch one page while validating each redirect before following it."""
    current = validate_public_url(url, resolver=resolver)
    get = request_get or requests.get
    redirects = 0
    while True:
        host = _host_name(current)
        if host != expected_host:
            raise ConversionError(f"Redirect crossed outside the requested hostname ({expected_host})")
        lock = _polite_lock(host)
        with lock:
            now = time.monotonic()
            delay = POLITE_DELAY_SECONDS - (now - _host_last_request.get(host, 0.0))
            if delay > 0:
                time.sleep(delay)
            _host_last_request[host] = time.monotonic()
            try:
                response = get(
                    current,
                    headers={"User-Agent": "GreedyGobbler/1.0 (local document extraction)", "Accept": "text/html,application/xhtml+xml"},
                    timeout=timeout,
                    allow_redirects=False,
                    stream=True,
                )
            except requests.RequestException as exc:
                raise ConversionError(f"Could not fetch {current}: {exc}") from exc

        try:
            status = int(response.status_code)
            headers = dict(response.headers or {})
            if status in {301, 302, 303, 307, 308}:
                location = headers.get("Location") or headers.get("location")
                if not location:
                    raise ConversionError(f"Page {current} returned a redirect without a Location header")
                if redirects >= MAX_REDIRECTS:
                    raise ConversionError(f"Page exceeded the {MAX_REDIRECTS}-redirect limit")
                next_url = validate_public_url(urljoin(current, location), resolver=resolver)
                if _host_name(next_url) != expected_host:
                    raise ConversionError(f"Redirect crossed outside the requested hostname ({expected_host})")
                current = next_url
                redirects += 1
                continue
            if status >= 400:
                raise ConversionError(f"Page returned HTTP {status}: {current}")
            body = _read_response_body(response, max_body_bytes)
            return HttpPage(
                url=current,
                body=body,
                content_type=str(headers.get("Content-Type", "")),
                status_code=status,
            )
        finally:
            close = getattr(response, "close", None)
            if close:
                close()


def _decode_body(page: HttpPage) -> str:
    encoding = "utf-8"
    match = re.search(r"charset\s*=\s*['\"]?([^;\s'\"]+)", page.content_type, re.I)
    if match:
        encoding = match.group(1)
    try:
        return page.body.decode(encoding, errors="replace")
    except LookupError:
        return page.body.decode("utf-8", errors="replace")


def _page_to_markdown(html: str, page_url: str) -> tuple[str, str, list[str]]:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    candidates = soup.find_all(["main", "article"])
    candidates.extend(soup.select('[role="main"]'))
    root = max(candidates, key=lambda node: len(node.get_text(" ", strip=True)), default=None)
    if root is None:
        root = soup.body or soup
    text = _epub_children(root).strip()
    cleaned_root = BeautifulSoup(str(root), "html.parser")
    for tag in cleaned_root.find_all(["nav", "script", "style"]):
        tag.decompose()
    cleaned_text = _epub_children(cleaned_root).strip()
    if title and not text.startswith("#"):
        text = f"# {title}\n\n{text}" if text else f"# {title}"
    if title and not cleaned_text.startswith("#"):
        cleaned_text = f"# {title}\n\n{cleaned_text}" if cleaned_text else f"# {title}"

    links = []
    for anchor in root.find_all("a", href=True):
        href = str(anchor.get("href", "")).strip()
        if not href:
            continue
        try:
            linked = normalize_url(urljoin(page_url, href))
        except ConversionError:
            # Articles commonly contain phone, email, download, and app links.
            # They are not crawl targets and must not invalidate the article.
            continue
        links.append(linked)
    return text, clean(cleaned_text, profile="document"), links


async def _browser_html(url: str) -> tuple[str, str]:
    try:
        from crawl4ai import AsyncWebCrawler
    except ImportError as exc:
        raise ConversionError("Browser extraction was requested but Crawl4AI is not installed") from exc
    requested_host = _host_name(url)
    blocked_requests = []

    async def on_page_context_created(page, context, **_kwargs):
        async def check_route(route):
            request_url = route.request.url
            if request_url.startswith(("data:", "blob:", "about:")):
                await route.continue_()
                return
            try:
                normalized = await asyncio.to_thread(validate_public_url, request_url)
                if _host_name(normalized) != requested_host:
                    raise ConversionError("Browser request crossed outside the requested hostname")
            except ConversionError as exc:
                blocked_requests.append(str(exc))
                await route.abort()
                return
            await route.continue_()

        await context.route("**", check_route)
        return page

    try:
        async with AsyncWebCrawler() as crawler:
            strategy = getattr(crawler, "crawler_strategy", None)
            if strategy is None or not callable(getattr(strategy, "set_hook", None)):
                raise ConversionError("Installed Crawl4AI does not expose the request hook needed for safe browser extraction")
            strategy.set_hook("on_page_context_created", on_page_context_created)
            result = await crawler.arun(url=url)
        if blocked_requests:
            raise ConversionError(f"Browser extraction blocked an unsafe request: {blocked_requests[0]}")
        if not getattr(result, "success", False):
            raise ConversionError(f"Browser extraction could not load {url}")
        final_url = getattr(result, "url", None) or url
        final_url = validate_public_url(final_url)
        if _host_name(final_url) != requested_host:
            raise ConversionError("Browser redirect crossed outside the requested hostname")
        html = getattr(result, "html", None) or getattr(result, "cleaned_html", None) or ""
        return html, final_url
    except ConversionError:
        raise
    except Exception as exc:
        raise ConversionError(f"Browser extraction failed for {url}: {exc}") from exc


def _microlink_markdown(url: str) -> str:
    try:
        response = requests.get(
            "https://api.microlink.io/",
            params={"url": url, "data.article.attr": "markdown", "meta": "false"},
            timeout=(5, 20),
        )
        response.raise_for_status()
        data = response.json()
        if data.get("status") == "success":
            return str(data.get("data", {}).get("article") or "")
    except requests.RequestException:
        return ""
    except (ValueError, AttributeError):
        return ""
    return ""


def _fetch_and_extract(url: str, expected_host: str, resolver=None, request_get=None) -> tuple[str, str, str, list[str]]:
    page = fetch_page(url, expected_host=expected_host, resolver=resolver, request_get=request_get)
    html = _decode_body(page)
    if "html" not in page.content_type.lower() and not html.lstrip().startswith(("<", "<!doctype")):
        raise ConversionError(f"Page did not return HTML content: {page.url}")
    text, cleaned, links = _page_to_markdown(html, page.url)
    return page.url, text, cleaned, links


def _is_url_safety_error(message: str) -> bool:
    lowered = message.lower()
    return any(
        marker in lowered
        for marker in (
            "not allowed",
            "redirect crossed",
            "did not resolve",
            "invalid address",
            "username or password",
            "only http and https",
            "local and .local",
            "unsafe browser request",
        )
    )


def extract_url_pages(
    seed_url: str,
    source_id: str,
    *,
    depth: int = 1,
    profile: str = "web",
    allow_remote: bool = False,
    use_browser: bool = False,
    progress_state: dict | None = None,
    progress=None,
    resolver=None,
    request_get=None,
) -> tuple[list[dict], list[str], dict]:
    """Fetch up to 25 same-host pages in deterministic breadth-first order."""
    seed = validate_public_url(seed_url, resolver=resolver)
    expected_host = _host_name(seed)
    pages_attempted = 0
    queued = [seed]
    enqueued = {seed}
    results = []
    warnings = []
    progress_target = progress_state if progress_state is not None else {"pages_done": 0, "pages_queued": 0}
    progress_target.clear()
    progress_target.update({"pages_done": 0, "pages_queued": 1})

    def report_progress() -> None:
        if callable(progress):
            progress(dict(progress_target))

    report_progress()
    level = 1
    while queued and level <= depth and pages_attempted < MAX_PAGES:
        available = MAX_PAGES - pages_attempted
        batch = queued[:available]
        queued = []
        pages_attempted += len(batch)
        with ThreadPoolExecutor(max_workers=min(3, len(batch))) as executor:
            futures = [executor.submit(_fetch_and_extract, url, expected_host, resolver, request_get) for url in batch]
            fetched = []
            for url, future in zip(batch, futures):
                try:
                    fetched.append(future.result())
                except Exception as exc:
                    message = str(exc) if isinstance(exc, ConversionError) else f"Could not extract {url}: {exc}"
                    fetched.append((url, "", "", [], message))
        for requested_url, fetched_page in zip(batch, fetched):
            if len(fetched_page) == 5:
                final_url, text, cleaned_text, links, local_error = fetched_page
            else:
                final_url, text, cleaned_text, links = fetched_page
                local_error = ""
            progress_target["pages_done"] += 1
            report_progress()
            if local_error:
                warnings.append(local_error)
                if _is_url_safety_error(local_error):
                    continue
                final_url = requested_url
            if len(re.sub(r"\s+", " ", text).strip()) < 120:
                if use_browser:
                    try:
                        validate_public_url(final_url, resolver=resolver)
                        browser_html, browser_final_url = asyncio.run(_browser_html(final_url))
                        final_url = browser_final_url
                        browser_text, browser_cleaned, browser_links = _page_to_markdown(browser_html, browser_final_url)
                        if len(browser_text.strip()) > len(text.strip()):
                            text = browser_text
                            cleaned_text = browser_cleaned
                            links.extend(browser_links)
                    except ConversionError as exc:
                        warnings.append(str(exc))
                    except RuntimeError as exc:
                        warnings.append(f"Browser extraction could not run: {exc}")
                if allow_remote and len(text.strip()) < 120:
                    remote_text = _microlink_markdown(final_url)
                    if len(remote_text.strip()) > len(text.strip()):
                        text = remote_text
                        cleaned_text = clean(remote_text, profile=profile)
                if len(text.strip()) < 120 and not local_error:
                    warnings.append(f"{final_url}: little static article text was available; JavaScript-heavy content may be missing.")
            if not text.strip():
                continue
            unit_number = len(results) + 1
            results.append({
                "id": f"{source_id}-U{unit_number:04d}",
                "source_id": source_id,
                "locator": final_url,
                "raw": text.strip(),
                "text": clean(cleaned_text, profile=profile),
            })

            if level < depth:
                for link in links:
                    try:
                        normalized = validate_public_url(link, resolver=resolver)
                    except ConversionError:
                        continue
                    if _host_name(normalized) != expected_host:
                        continue
                    if normalized in enqueued:
                        continue
                    if len(enqueued) >= MAX_PAGES:
                        continue
                    enqueued.add(normalized)
                    queued.append(normalized)
        progress_target["pages_queued"] = len(enqueued)
        report_progress()
        level += 1

    if not results:
        detail = next((warning for warning in warnings if warning), "The page returned no extractable article content")
        raise ConversionError(detail)
    return results, warnings, {
        "unit_count": len(results),
        "pages_attempted": pages_attempted,
        "pages_succeeded": len(results),
        "pages_queued": len(enqueued),
    }
