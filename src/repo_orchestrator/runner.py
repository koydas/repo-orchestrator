"""The M1 pipeline as one function: ``collect → evaluate → report → (persist)``. No framework, no services."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .collector.source import SnapshotSource
from .engine import Engine, RunOptions, ScanResult
from .models import digest
from .report import build_semantic, exit_code
from .store import RecordResult, RunStore


class DeterminismError(RuntimeError):
    """Two complete runs with identical inputs produced different normalized results."""


@dataclass
class ScanReport:
    semantic: dict[str, Any]
    execution: dict[str, Any]
    result: ScanResult
    exit_code: int
    record: RecordResult | None


def run_scan(
    engine: Engine,
    source: SnapshotSource,
    options: RunOptions,
    *,
    store: RunStore | None = None,
    overlay_digest: str | None = None,
    stats: Callable[[], dict[str, int]] | None = None,
) -> ScanReport:
    started = datetime.now(UTC)
    t0 = time.monotonic()
    result = engine.run(source, options)
    semantic = build_semantic(result, engine.decl, requested=options.revisions, overlay_digest=overlay_digest)
    enumerated = {p.meta.id: p.meta.enumerated_subjects for p in engine.policies.values() if p.meta.enumerated_subjects}
    record = store.record(semantic, result.outcomes, enumerated=enumerated) if store else None
    if store is not None and semantic["run_status"] == "complete":
        divergent = store.divergent_complete_runs(digest(semantic["inputs"]))
        if divergent:
            raise DeterminismError(f"identical inputs produced {len(divergent)} different results: {divergent}")
    execution = {
        "run_id": uuid.uuid4().hex,
        "started_at": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "duration_ms": round((time.monotonic() - t0) * 1000),
        "stats": stats() if stats else {},
        "persisted": record is not None,
        "run_key": record.run_key if record else None,
        "new_run": record.new if record else None,
        "history": record.transitions if record else {},
    }
    return ScanReport(semantic, execution, result, exit_code(semantic), record)
