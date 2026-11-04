"""W3 as one function behind ``scan --investigate``: candidates (P7 pre-filter and intent reviews) → run created in the
store → workflow (fan-out, checkpoints, interrupts) → report section. Idempotent: the run id is derived from the inputs, so
repeating the command continues, finishes or simply re-reports the same run (ADR-0016 §4)."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..collector.cache import CachingSource
from ..collector.snapshot import Artifact, Limits, RepoTarget, collect
from ..collector.source import SnapshotSource
from ..declarations import load_declarations
from ..engine import Engine
from ..evidence import verify as verify_evidence
from ..models import ConfigError, digest
from ..runner import ScanReport
from .budgets import BUDGET, MODEL, PROMPT_VERSION, Budget
from .domain import Candidate, candidate_from_json, candidate_to_json
from .investigator import Investigator
from .p7 import Claims, build_candidates, claims_summary, load_claims
from .provider import NoProvider, Provider, Recording, ReplayProvider
from .reader import PinnedReader
from .report import augment, build_section, execution
from .report import exit_code as investigation_exit_code
from .runner import Orchestrator
from .store import InvestigationStore


class SnapshotChanged(ConfigError):
    """A resumed run's pinned inputs differ from what it recorded (ADR-0016 §4): fail closed, never re-pin."""


@dataclass
class InvestigationOptions:
    declarations_dir: Path
    cache_dir: Path
    state_db: Path
    checkpoint_path: Path
    provider: Provider | None = None
    recording: Recording | None = None  # --replay: answers come from this recording only
    declarations_digest: str = ""
    revisions: dict[str, str] = field(default_factory=dict)
    offline: bool = False
    budget: Budget = BUDGET  # tests only: the CLI never sets it (budgets are code constants, ADR-0014 §4)


@dataclass
class Investigated:
    semantic: dict[str, Any]
    execution: dict[str, Any]
    exit_code: int
    run_id: str
    phase: str
    store: InvestigationStore


def _pinned(scan: ScanReport) -> dict[str, str]:
    return {r["repo"]: r["sha"] for r in scan.semantic["inputs"]["revisions"] if r["sha"]}


def make_run(
    store: InvestigationStore, engine: Engine, scan: ScanReport, source: SnapshotSource, opts: InvestigationOptions
) -> tuple[dict[str, Any], PinnedReader, Claims, list[dict[str, str]]]:
    claims = load_claims(opts.declarations_dir, engine)
    scope = scan.result.scope
    reader = PinnedReader(scan.result.snapshot, source, scope)
    candidates, skipped = build_candidates(claims, engine, reader, scan.result.outcomes, scope)
    cand_json = [candidate_to_json(c) for c in candidates]
    cdigest = digest(cand_json)
    run_id = digest(
        {"inputs": scan.semantic["inputs"], "claims": claims.digest, "candidates": cdigest, "model": MODEL,
         "prompt": PROMPT_VERSION, "budget": dataclasses.asdict(BUDGET)}
    )[:16]  # fmt: skip
    config = {
        "candidates_digest": cdigest, "declarations": str(opts.declarations_dir), "declarations_digest": opts.declarations_digest,
        "cache_dir": str(opts.cache_dir), "revisions": _pinned(scan), "scope": list(scope), "skipped": skipped,
        "claims": dict(claims_summary(claims)),
    }  # fmt: skip
    store.create_run(run_id, digest(scan.semantic["inputs"]), config, cand_json)
    run = store.run(run_id)
    assert run is not None
    return run, reader, claims, skipped


def _provider(opts: InvestigationOptions) -> Provider:
    if opts.recording is not None:
        return ReplayProvider(Recording())  # misses fail closed; the recording lives in the store
    return opts.provider or NoProvider()


def run_investigation(
    engine: Engine, scan: ScanReport, source: SnapshotSource, opts: InvestigationOptions
) -> Investigated:
    store = InvestigationStore(opts.state_db, opts.budget)
    if opts.recording is not None:
        store.import_recording(opts.recording)
    run, reader, _claims, _skipped = make_run(store, engine, scan, source, opts)
    orch = Orchestrator(
        store,
        opts.checkpoint_path,
        Investigator(_provider(opts), store, reader, run["run_id"], opts.budget).investigate,
        opts.budget,
    )
    try:
        status = orch.advance(run["run_id"])
    finally:
        orch.close()
    return assemble(
        store, run["run_id"], scan.semantic, status["phase"], "replay" if opts.recording is not None else "live"
    )


def assemble(store: InvestigationStore, run_id: str, semantic: dict[str, Any], phase: str, mode: str) -> Investigated:
    run = store.run(run_id)
    assert run is not None
    section = build_section(store, run, run["config"]["claims"], run["config"]["skipped"])
    augmented = augment(semantic, section)
    return Investigated(
        augmented, execution(store, run, mode, phase), investigation_exit_code(augmented), run_id, phase, store
    )


# ---- resume (a stored run, no scan) -----------------------------------------------------------------------------------


def rebuild_reader(run: dict[str, Any]) -> PinnedReader:
    """The run's snapshots rebuilt OFFLINE from the cache at the SHAs it recorded — nothing is re-resolved — then every
    piece of deterministic evidence is re-verified against them (content-addressed: fail closed, ADR-0016 §4)."""
    cfg = run["config"]
    decl = load_declarations(Path(cfg["declarations"]))
    if cfg["declarations_digest"] and decl.digest != cfg["declarations_digest"]:
        raise SnapshotChanged("the declarations changed since this run was created; start a new run")
    engine = Engine(decl)
    source = CachingSource(None, Path(cfg["cache_dir"]))
    scope = tuple(cfg["scope"])
    targets = [RepoTarget(r, engine.graph.repositories[r].slug, cfg["revisions"].get(r)) for r in scope]
    snapshot = collect(source, targets, engine.plan(set(scope)), Limits())
    reader = PinnedReader(snapshot, source, scope)
    for c in (candidate_from_json(d) for d in run["candidates"]):
        for e in c.evidence:
            ok = verify_evidence(e, snapshot)
            if not ok and e.kind == "text":
                art = reader.artifact(e.repo, e.path)
                ok = isinstance(art, Artifact) and art.sha == e.sha and art.content_sha256 == e.content_sha256
            if not ok:
                raise SnapshotChanged(
                    f"candidate {c.key}: evidence {e.repo}:{e.path} no longer matches the pinned snapshot"
                )
    return reader


def resume_run(run_id: str, opts: InvestigationOptions) -> tuple[InvestigationStore, str]:
    store = InvestigationStore(opts.state_db, opts.budget)
    run = store.run(run_id)
    if run is None:
        raise ConfigError(f"unknown investigation run {run_id}")
    reader = rebuild_reader(run)
    orch = Orchestrator(
        store,
        opts.checkpoint_path,
        Investigator(_provider(opts), store, reader, run_id, opts.budget).investigate,
        opts.budget,
    )
    try:
        status = orch.advance(run_id)
    finally:
        orch.close()
    return store, status["phase"]


def candidates_of(run: dict[str, Any]) -> list[Candidate]:
    return [candidate_from_json(d) for d in run["candidates"]]


def open_orchestrator(store: InvestigationStore, opts: InvestigationOptions) -> Orchestrator:
    """An orchestrator for transitions that never investigate (decide, approve, status): a call that is not recorded fails."""

    def refuse(c: Candidate) -> Any:
        raise ConfigError("this command does not investigate; use 'investigations resume'")

    return Orchestrator(store, opts.checkpoint_path, refuse, opts.budget)
