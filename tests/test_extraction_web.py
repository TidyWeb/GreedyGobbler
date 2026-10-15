"""Deterministic HTTP extraction and safety regressions."""

from __future__ import annotations

import time
import unittest
import ipaddress
from unittest import mock

import converter
from pipeline import web_fetch


SAFE_ADDRESS = "93.184.216.34"


def safe_resolver(host, _port):
    try:
        return [str(ipaddress.ip_address(host))]
    except ValueError:
        return [SAFE_ADDRESS]


class FakeResponse:
    def __init__(self, body="", status=200, headers=None):
        self.status_code = status
        self.headers = {"Content-Type": "text/html; charset=utf-8", **(headers or {})}
        self.body = body.encode("utf-8") if isinstance(body, str) else body
        self.closed = False

    def iter_content(self, chunk_size=65536):
        for start in range(0, len(self.body), chunk_size):
            yield self.body[start:start + chunk_size]

    def close(self):
        self.closed = True


def article(title, body, links=""):
    return f"""<!doctype html><html><head><title>{title}</title></head><body>
      <nav><p>Site navigation</p></nav>
      <main><h1>{title}</h1><p>{body}</p>{links}</main>
    </body></html>"""


class UrlValidationTests(unittest.TestCase):
    def test_http_urls_normalize_fragments_and_reject_credentials(self):
        self.assertEqual(web_fetch.normalize_url("https://example.com/a#part"), "https://example.com/a")
        with self.assertRaisesRegex(converter.ConversionError, "usernames or passwords"):
            web_fetch.normalize_url("https://phil:secret@example.com/")
        with self.assertRaisesRegex(converter.ConversionError, "HTTP and HTTPS"):
            web_fetch.normalize_url("file:///etc/passwd")

    def test_private_loopback_and_reserved_addresses_are_rejected(self):
        for address in ("127.0.0.1", "10.2.3.4", "169.254.1.1", "[::1]"):
            with self.subTest(address=address):
                with self.assertRaisesRegex(converter.ConversionError, "not allowed"):
                    web_fetch.validate_public_url(f"http://{address}/")
        with self.assertRaisesRegex(converter.ConversionError, "Local and .local"):
            web_fetch.validate_public_url("http://router.local/")

    def test_redirects_are_revalidated_and_cannot_cross_hosts(self):
        seen = []

        def fake_get(url, **_kwargs):
            seen.append(url)
            return FakeResponse(status=302, headers={"Location": "http://127.0.0.1/admin"})

        with mock.patch.object(web_fetch, "_resolve_public_addresses", side_effect=safe_resolver):
            with self.assertRaisesRegex(converter.ConversionError, "not allowed"):
                web_fetch.fetch_page("https://example.com/start", expected_host="example.com", request_get=fake_get)
        self.assertEqual(seen, ["https://example.com/start"])

    def test_cross_hostname_redirect_is_rejected_before_following(self):
        seen = []

        def fake_get(url, **_kwargs):
            seen.append(url)
            return FakeResponse(status=302, headers={"Location": "https://other.example/page"})

        with mock.patch.object(web_fetch, "_resolve_public_addresses", side_effect=safe_resolver):
            with self.assertRaisesRegex(converter.ConversionError, "Redirect crossed"):
                web_fetch.fetch_page("https://example.com/start", expected_host="example.com", request_get=fake_get)
        self.assertEqual(seen, ["https://example.com/start"])


class UrlExtractionTests(unittest.TestCase):
    def setUp(self):
        self.address_patch = mock.patch.object(web_fetch, "_resolve_public_addresses", side_effect=safe_resolver)
        self.address_patch.start()
        self.addCleanup(self.address_patch.stop)
        self.original_delay = web_fetch.POLITE_DELAY_SECONDS
        web_fetch.POLITE_DELAY_SECONDS = 0
        self.addCleanup(setattr, web_fetch, "POLITE_DELAY_SECONDS", self.original_delay)

    def test_relative_links_fragments_and_duplicate_targets_are_resolved_once(self):
        pages = {
            "https://example.com/start": article(
                "Start",
                "ROOT_ARTICLE_EVIDENCE " * 14,
                '<a href="/chapter#part">chapter</a><a href="chapter">duplicate</a><a href="tel:+441234567890">phone</a>',
            ),
            "https://example.com/chapter": article("Chapter", "CHAPTER_ARTICLE_EVIDENCE " * 14),
        }
        seen = []

        def fake_get(url, **_kwargs):
            seen.append(url)
            return FakeResponse(pages[url])

        with mock.patch.object(web_fetch.requests, "get", side_effect=fake_get):
            result = converter.extract_input(
                "https://example.com/start#top",
                is_url=True,
                depth=2,
                profile="web",
            )
        self.assertEqual(seen, ["https://example.com/start", "https://example.com/chapter"])
        self.assertEqual([unit["locator"] for unit in result["units"]], seen)
        self.assertIn("ROOT_ARTICLE_EVIDENCE", result["raw"])
        self.assertIn("CHAPTER_ARTICLE_EVIDENCE", result["raw"])
        self.assertEqual([unit["id"] for unit in result["units"]], ["S1-U0001", "S1-U0002"])

    def test_concurrent_level_results_keep_breadth_first_link_order(self):
        pages = {
            "https://example.com/start": article(
                "Start",
                "ROOT_ORDERING_EVIDENCE " * 14,
                '<a href="/slow">slow</a><a href="/fast">fast</a>',
            ),
            "https://example.com/slow": article("Slow", "SLOW_ORDERING_EVIDENCE " * 14),
            "https://example.com/fast": article("Fast", "FAST_ORDERING_EVIDENCE " * 14),
        }
        seen = []

        def fake_get(url, **_kwargs):
            seen.append(url)
            if url.endswith("/slow"):
                time.sleep(0.05)
            return FakeResponse(pages[url])

        with mock.patch.object(web_fetch.requests, "get", side_effect=fake_get):
            result = converter.extract_input("https://example.com/start", is_url=True, depth=2)
        self.assertEqual(
            [unit["locator"] for unit in result["units"]],
            ["https://example.com/start", "https://example.com/slow", "https://example.com/fast"],
        )

    def test_attempt_cap_includes_failed_pages(self):
        child_links = "".join(f'<a href="/child-{index}">child</a>' for index in range(40))
        root = article("Root", "ROOT_CAP_EVIDENCE " * 14, child_links)
        attempted = []

        def fake_get(url, **_kwargs):
            attempted.append(url)
            if url.endswith("/start"):
                return FakeResponse(root)
            return FakeResponse("unavailable", status=503)

        with mock.patch.object(web_fetch.requests, "get", side_effect=fake_get):
            result = converter.extract_input("https://example.com/start", is_url=True, depth=3)
        self.assertEqual(result["metrics"]["per_source"][0]["pages_attempted"], 25)
        self.assertEqual(len(attempted), 25)
        self.assertEqual(len(result["units"]), 1)
        self.assertEqual(len(result["warnings"]), 24)

    def test_progress_callback_and_compatibility_object_keep_stable_identity(self):
        before_id = id(converter.crawl_progress)
        updates = []
        page = article("Progress", "PROGRESS_EVIDENCE " * 14)

        with mock.patch.object(web_fetch.requests, "get", return_value=FakeResponse(page)):
            converter.extract_input("https://example.com/progress", is_url=True, progress=updates.append)
        self.assertEqual(id(converter.crawl_progress), before_id)
        self.assertEqual(converter.crawl_progress, {"pages_done": 1, "pages_queued": 1})
        self.assertEqual(updates[-1], converter.crawl_progress)

    def test_microlink_is_not_called_for_normal_local_first_extraction(self):
        page = article("Local", "LOCAL_FIRST_EVIDENCE " * 14)
        called = []

        def fake_get(url, **_kwargs):
            called.append(url)
            return FakeResponse(page)

        with mock.patch.object(web_fetch.requests, "get", side_effect=fake_get):
            converter.extract_input("https://example.com/local", is_url=True)
        self.assertEqual(called, ["https://example.com/local"])
        self.assertFalse(any("microlink" in url for url in called))

    def test_page_limit_and_http_errors_are_not_returned_as_content(self):
        def fail_get(url, **_kwargs):
            return FakeResponse("Error crawling URL: gateway failure", status=503)

        with mock.patch.object(web_fetch.requests, "get", side_effect=fail_get):
            with self.assertRaisesRegex(converter.ConversionError, "HTTP 503"):
                converter.extract_input("https://example.com/failing", is_url=True)

    def test_url_cleaning_removes_dom_chrome_but_keeps_raw_and_phone_links_do_not_fail(self):
        html = """<html><head><title>DOM Evidence</title><style>CSS_CHROME</style>
          <script>JS_CHROME</script></head><body>
          <main><nav>NAV_CHROME</nav><style>CSS_CHROME</style><script>JS_CHROME</script>
          <p>DOM_BODY_EVIDENCE with sufficient visible source content. """ + ("evidence " * 20) + """
          </p><a href="tel:+441234567890">PHONE_LINK</a></main></body></html>"""
        with mock.patch.object(web_fetch.requests, "get", return_value=FakeResponse(html)):
            result = converter.extract_input("https://example.com/dom", is_url=True)
        for marker in ("CSS_CHROME", "JS_CHROME", "NAV_CHROME"):
            self.assertIn(marker, result["raw"])
            self.assertNotIn(marker, result["cleaned"])
        self.assertIn("DOM_BODY_EVIDENCE", result["cleaned"])

    def test_url_body_trim_keeps_raw_pages_and_empties_omitted_unit_text(self):
        root = article("Preface", "URL_FRONT_MATTER_EVIDENCE " * 20, '<a href="/chapter">chapter</a>')
        chapter = article("Chapter 1", "URL_CHAPTER_BODY_EVIDENCE " * 20)
        pages = {"https://example.com/book": root, "https://example.com/chapter": chapter}

        def fake_get(url, **_kwargs):
            return FakeResponse(pages[url])

        with mock.patch.object(web_fetch.requests, "get", side_effect=fake_get):
            full = converter.extract_input("https://example.com/book", is_url=True, depth=2)
            trimmed = converter.extract_input("https://example.com/book", is_url=True, depth=2, trim_body=True)
        self.assertEqual(trimmed["raw"], full["raw"])
        self.assertEqual(trimmed["units"][0]["text"], "")
        self.assertIn("URL_FRONT_MATTER_EVIDENCE", trimmed["raw"])
        self.assertNotIn("URL_FRONT_MATTER_EVIDENCE", trimmed["cleaned"])
        self.assertIn("URL_CHAPTER_BODY_EVIDENCE", trimmed["cleaned"])

    def test_browser_fallback_runs_for_empty_javascript_page_only_when_requested(self):
        empty_html = "<html><head><title>App</title></head><body><main></main></body></html>"
        rendered_html = article("Rendered App", "BROWSER_RENDERED_EVIDENCE " * 18)
        browser = mock.AsyncMock(return_value=(rendered_html, "https://example.com/app"))
        with mock.patch.object(web_fetch.requests, "get", return_value=FakeResponse(empty_html)):
            with mock.patch.object(web_fetch, "_browser_html", browser):
                result = converter.extract_input("https://example.com/app", is_url=True, use_browser=True)
        self.assertIn("BROWSER_RENDERED_EVIDENCE", result["raw"])
        browser.assert_awaited_once()

    def test_browser_fallback_can_recover_from_a_safe_static_http_failure(self):
        rendered_html = article("Rendered Error Page", "BROWSER_AFTER_HTTP_FAILURE " * 18)
        browser = mock.AsyncMock(return_value=(rendered_html, "https://example.com/rendered"))
        with mock.patch.object(web_fetch.requests, "get", return_value=FakeResponse("", status=503)):
            with mock.patch.object(web_fetch, "_browser_html", browser):
                result = converter.extract_input("https://example.com/rendered", is_url=True, use_browser=True)
        self.assertIn("BROWSER_AFTER_HTTP_FAILURE", result["raw"])
        self.assertTrue(any("HTTP 503" in warning for warning in result["warnings"]))

    def test_microlink_fallback_requires_explicit_remote_opt_in(self):
        remote_text = "REMOTE_OPT_IN_EVIDENCE " * 20
        with mock.patch.object(web_fetch.requests, "get", return_value=FakeResponse("", status=503)):
            with mock.patch.object(web_fetch, "_microlink_markdown", return_value=remote_text) as microlink:
                result = converter.extract_input("https://example.com/remote", is_url=True, allow_remote=True)
        self.assertIn("REMOTE_OPT_IN_EVIDENCE", result["raw"])
        microlink.assert_called_once_with("https://example.com/remote")

    def test_unsafe_static_redirect_does_not_fall_through_to_browser(self):
        def private_redirect(_url, **_kwargs):
            return FakeResponse(status=302, headers={"Location": "http://127.0.0.1/admin"})

        browser = mock.AsyncMock(return_value=(article("Unsafe", "SHOULD_NOT_APPEAR " * 20), "http://127.0.0.1/admin"))
        with mock.patch.object(web_fetch.requests, "get", side_effect=private_redirect):
            with mock.patch.object(web_fetch, "_browser_html", browser):
                with self.assertRaisesRegex(converter.ConversionError, "not allowed"):
                    converter.extract_input("https://example.com/unsafe", is_url=True, use_browser=True)
        browser.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
