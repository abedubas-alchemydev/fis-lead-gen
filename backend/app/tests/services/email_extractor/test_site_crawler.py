"""Site-crawler tests.

All HTTP via respx — no real network. Constructor uses request_delay=0 so
tests don't burn 3+ seconds on the rate-limit sleep.
"""

from __future__ import annotations

import httpx
import respx

from app.services.email_extractor.site_crawler import SiteCrawler


def _crawler() -> SiteCrawler:
    return SiteCrawler(request_delay_seconds=0.0)


@respx.mock
async def test_happy_path_finds_mailto_text_and_obfuscated() -> None:
    respx.get("https://example.com/robots.txt").mock(return_value=httpx.Response(200, text=""))
    respx.get("https://example.com/").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text=(
                "<html><body>"
                '<a href="mailto:contact@example.com">Contact</a>'
                "<p>support@example.com is great.</p>"
                "<p>obscure: hello [at] example [dot] com</p>"
                "</body></html>"
            ),
        )
    )
    for path in ("/contact", "/about", "/team", "/staff", "/people"):
        respx.get(f"https://example.com{path}").mock(return_value=httpx.Response(404))

    result = await _crawler().run("example.com")
    emails = sorted(d.email for d in result.emails)
    assert emails == ["contact@example.com", "hello@example.com", "support@example.com"]
    by_email = {d.email: d for d in result.emails}
    assert by_email["contact@example.com"].confidence == 0.75
    assert by_email["support@example.com"].confidence == 0.6
    assert by_email["hello@example.com"].confidence == 0.6
    assert all(d.attribution == "https://example.com/" for d in result.emails)


@respx.mock
async def test_robots_disallow_all_returns_empty_no_error() -> None:
    respx.get("https://example.com/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /\n")
    )
    # Crawler still does the resolve_base_url probe before reading robots.
    respx.get("https://example.com/").mock(
        return_value=httpx.Response(200, headers={"content-type": "text/html"}, text="<html></html>")
    )

    result = await _crawler().run("example.com")
    assert result.emails == []
    assert result.errors == []


@respx.mock
async def test_homepage_500_records_error_and_returns_empty() -> None:
    respx.get("https://example.com/robots.txt").mock(return_value=httpx.Response(200, text=""))
    respx.get("https://example.com/").mock(return_value=httpx.Response(500))
    for path in ("/contact", "/about", "/team", "/staff", "/people"):
        respx.get(f"https://example.com{path}").mock(return_value=httpx.Response(404))

    result = await _crawler().run("example.com")
    assert result.emails == []
    assert any("500" in err for err in result.errors)


@respx.mock
async def test_non_html_content_type_is_skipped() -> None:
    respx.get("https://example.com/robots.txt").mock(return_value=httpx.Response(200, text=""))
    respx.get("https://example.com/").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "application/pdf"},
            content=b"%PDF-1.4 fake@example.com",
        )
    )
    for path in ("/contact", "/about", "/team", "/staff", "/people"):
        respx.get(f"https://example.com{path}").mock(return_value=httpx.Response(404))

    result = await _crawler().run("example.com")
    assert result.emails == []


@respx.mock
async def test_off_domain_email_is_dropped() -> None:
    respx.get("https://example.com/robots.txt").mock(return_value=httpx.Response(200, text=""))
    respx.get("https://example.com/").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text=('<html><body><a href="mailto:noreply@google.com">Spam</a><p>real@example.com</p></body></html>'),
        )
    )
    for path in ("/contact", "/about", "/team", "/staff", "/people"):
        respx.get(f"https://example.com{path}").mock(return_value=httpx.Response(404))

    result = await _crawler().run("example.com")
    emails = sorted(d.email for d in result.emails)
    assert emails == ["real@example.com"]


@respx.mock
async def test_dedupes_same_email_from_two_pages() -> None:
    respx.get("https://example.com/robots.txt").mock(return_value=httpx.Response(200, text=""))
    page = '<html><body><a href="mailto:hello@example.com">x</a></body></html>'
    respx.get("https://example.com/").mock(
        return_value=httpx.Response(200, headers={"content-type": "text/html"}, text=page)
    )
    respx.get("https://example.com/contact").mock(
        return_value=httpx.Response(200, headers={"content-type": "text/html"}, text=page)
    )
    for path in ("/about", "/team", "/staff", "/people"):
        respx.get(f"https://example.com{path}").mock(return_value=httpx.Response(404))

    result = await _crawler().run("example.com")
    assert [d.email for d in result.emails] == ["hello@example.com"]
