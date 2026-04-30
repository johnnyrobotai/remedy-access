from __future__ import annotations

LEGACY_TRANSCRIPT_PIPELINE_VERSION = "designed-page-v1"
STRUCTURED_RENDER_V2_TRANSCRIPT_PIPELINE_VERSION = "structured-render-v2.12"

# Kept as the default/current legacy label for call sites and tests that still
# refer to the pre-v2 pipeline explicitly.
TRANSCRIPT_PIPELINE_VERSION = LEGACY_TRANSCRIPT_PIPELINE_VERSION

CURRENT_TRANSCRIPT_PIPELINE_VERSIONS = frozenset({
    LEGACY_TRANSCRIPT_PIPELINE_VERSION,
    STRUCTURED_RENDER_V2_TRANSCRIPT_PIPELINE_VERSION,
})


def is_current_transcript_pipeline(pipeline_version: str | None) -> bool:
    return bool(pipeline_version) and pipeline_version in CURRENT_TRANSCRIPT_PIPELINE_VERSIONS
