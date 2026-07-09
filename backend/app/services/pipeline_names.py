"""Canonical pipeline-name string constants — single source of truth.

Dependency-free by design: importing this module pulls in NO models, services,
or provider stacks, so lightweight consumers (the pipeline reaper, the ``/enrich``
in-flight dispatch guard) can reference a pipeline's canonical name WITHOUT
importing the heavy orchestrator that implements it.

Both the ``favorite_list_enrich`` orchestrator (which stamps this name onto the
parent :class:`~app.models.pipeline_run.PipelineRun`) and the matchers that key
off it — the reaper's ``REFRESH_FAMILY_PIPELINES`` staleness family and the
endpoint's in-flight guard — read the SAME constant from here. A rename therefore
lands in exactly one place and can never silently desync the writer from the
matchers (which would let a live run read as orphaned and get double-spent/reaped).
"""

from __future__ import annotations

# Bulk "enrich a whole favorite list" background worker. Written onto the parent
# PipelineRun by favorite_list_enrich_orchestrator; matched by the reaper's
# REFRESH_FAMILY_PIPELINES and the POST /favorite-lists/{id}/enrich in-flight
# dispatch guard.
FAVORITE_LIST_ENRICH_PIPELINE = "favorite_list_enrich"

__all__ = ["FAVORITE_LIST_ENRICH_PIPELINE"]
