"""Typed state, reducers and report normalization."""
import hashlib
import json
import operator
from typing import Annotated, Any, TypedDict

from .errors import ReducerConflict

OUTCOMES = ("verified", "supported", "insufficient_evidence", "failed")
RETRIABLE_REASONS = ("agent_exception", "malformed_output")
TERMINAL_STATUSES = ("approved", "rejected")


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest(obj: Any) -> str:
    return hashlib.sha256(canonical(obj).encode()).hexdigest()


class RunInput(TypedDict):
    """The only thing a caller can supply when starting a run. No budgets, counters or outcomes."""
    run_id: str
    scenario: str


class Evidence(TypedDict):
    repo: str
    sha: str
    path: str
    quote: str


class InvestigationResult(TypedDict):
    candidate_id: str
    attempt: int  # 0 = never investigated (denied before any attempt)
    outcome: str  # one of OUTCOMES, assigned by code, never by the agent
    reason: str
    claim: str
    evidence: list  # only evidence whose quote was verified in code
    units_spent: int
    agent_asserted_outcome: Any  # audit only: what the scripted agent said about itself, never used


def result_key(candidate_id: str, attempt: int) -> str:
    return f"{candidate_id}#{attempt}"


def keep_first(old, new):
    """Reducer: an identity field is written once; later writes (e.g. a second start input) are ignored."""
    return old if old else new


def merge_results(left: dict, right: dict) -> dict:
    """Reducer for parallel workers: union keyed by candidate#attempt.

    The same key with identical content is an idempotent replay (a re-executed task); the same key with
    different content is a duplicate execution that disagrees with itself and aborts the step.
    """
    out = dict(left or {})
    for k, v in (right or {}).items():
        if k in out and out[k] != v:
            raise ReducerConflict(f"conflicting results for {k}")
        out[k] = v
    return out


def merge_denials(left: dict, right: dict) -> dict:
    out = dict(left or {})
    for k, v in (right or {}).items():
        out.setdefault(k, v)  # the first recorded reason stands
    return out


class RunState(TypedDict, total=False):
    run_id: Annotated[str, keep_first]
    scenario: Annotated[str, keep_first]
    status: str  # running | awaiting_approval | approved | rejected
    meta: dict  # run metadata: nonce and start time; set once, nondeterministic, excluded from comparisons
    candidates: list  # frozen accepted candidates
    candidate_ids: list  # frozen: accepted + deferred
    input_digest: str
    batch: list  # work items of the current round
    results: Annotated[dict, merge_results]
    denials: Annotated[dict, merge_denials]
    spent: int  # investigations dispatched (mirror of the ledger, which is authoritative)
    rounds: int
    steps: Annotated[int, operator.add]
    integrity_errors: Annotated[list, operator.add]
    report: dict
    decision: dict
    outcome: dict


def latest_results(results: dict) -> dict:
    """candidate_id -> result with the highest attempt."""
    best: dict = {}
    for r in (results or {}).values():
        cur = best.get(r["candidate_id"])
        if cur is None or r["attempt"] > cur["attempt"]:
            best[r["candidate_id"]] = r
    return best


def normalize_report(report: dict) -> dict:
    """Semantic content of a report: drops run id, metadata and the digest itself."""
    return {k: report[k] for k in ("input_digest", "findings", "counts", "denials", "integrity_errors", "usage")}
