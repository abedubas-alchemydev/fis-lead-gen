"""Multi-provider contact discovery chain.

Providers implement :class:`ContactDiscoveryProvider` and are composed by
:func:`orchestrator.discover_contact`. The chain powers the "Generate More
Details" button on the firm detail page -- it walks one officer at a time
through Apollo-match -> Hunter -> Snov (configurable) until it finds a hit
above the confidence threshold.
"""

from app.services.contact_discovery.base import (
    ContactDiscoveryProvider,
    DiscoveryEntity,
    DiscoveryResult,
)

__all__ = ["ContactDiscoveryProvider", "DiscoveryEntity", "DiscoveryResult"]
