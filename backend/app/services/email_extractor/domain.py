"""Bank website → scan-domain resolver for the Email Extractor.

The Email Extractor keys every scan on a bare ``domain`` (e.g. ``example.com``).
Broker-dealer / advisor scans get theirs from the firm's already-normalized
website; banks store a raw ``banks.website`` that may carry a scheme, a
``www.`` (or other) subdomain, a path, a port, or trailing junk. This helper
turns that raw website into the registrable domain the scan should target so a
bank-scoped "Extract Email" launches against the bank's apex.

Kept dependency-free (no public-suffix list): the registrable domain is taken
as the last two DNS labels of the host. That is correct for the .com/.org/.net
/.us universe every FDIC/OCC bank charter lives in; it would over-truncate a
multi-part public suffix like ``co.uk`` (→ ``co.uk``), but no US bank charter
uses one, so the simplification is intentional and safe for this dataset.
"""

from __future__ import annotations

from urllib.parse import urlparse


def bank_domain_from_website(website: str | None) -> str | None:
    """Return the registrable domain for a bank ``website``, or ``None``.

    ``None`` when the website is missing / blank / has no parseable host — the
    caller then falls back to the client-supplied ``domain`` unchanged.

    Examples::

        "https://www.exchangebank.com/personal" -> "exchangebank.com"
        "Exchange Bank"                          -> None  (no host)
        "occ.gov"                                -> "occ.gov"
        "banking.first-fed.com:8443/login"       -> "first-fed.com"
    """
    if not website:
        return None
    candidate = website.strip()
    if not candidate:
        return None
    # urlparse only fills ``netloc`` when a scheme is present; a bare
    # "example.com/path" otherwise lands entirely in ``path``.
    if "://" not in candidate:
        candidate = f"http://{candidate}"
    host = (urlparse(candidate).hostname or "").strip().lower().rstrip(".")
    if not host:
        return None
    labels = [label for label in host.split(".") if label]
    if len(labels) < 2:
        # A bare hostname with no dot isn't a registrable domain.
        return None
    # Registrable domain = last two labels (naive eTLD+1; see module docstring).
    return ".".join(labels[-2:])
