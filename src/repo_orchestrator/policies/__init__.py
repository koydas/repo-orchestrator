"""C3 policies P1-P6. Each module exposes ``POLICY``; the registry fixes their order."""

from . import p1_counts, p2_json, p3_models, p4_allocation, p5_references, p6_tooling
from .base import Policy

REGISTRY: tuple[Policy, ...] = (
    p1_counts.POLICY,
    p2_json.POLICY,
    p3_models.POLICY,
    p4_allocation.POLICY,
    p5_references.POLICY,
    p6_tooling.POLICY,
)
