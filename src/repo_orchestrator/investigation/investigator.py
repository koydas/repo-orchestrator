"""C5 Investigator: for ONE candidate, read more of the pinned snapshots through read-only tools and return a PROPOSAL
that code then verifies and decides on (ADR-0004 §3, ADR-0016 §4).

Failure policy (docs/workflows.md → Failure handling), all per candidate: a provider error is retried once when it is
transient, malformed output once, a budget refusal ends the candidate; every end that is not a verified proposal is an
``insufficient_evidence`` result with a cause code. Nothing here raises for model or provider behaviour.
"""

from __future__ import annotations

from .budgets import BUDGET, MODEL, Budget
from .domain import Candidate, ICause, Observation, Result
from .prompt import SYSTEM_PROMPT, Malformed, ToolCall, opening_message, parse_reply, scan_instruction_like
from .provider import LLMRequest, LLMResponse, Provider, ProviderError, Recording, response_digest
from .reader import PinnedReader
from .store import InvestigationStore
from .tools import run_tool
from .verify import check_quotes, decide, failure_result

_REPAIR = (
    "Your previous reply was not accepted ({why}). Reply with exactly one JSON object in the documented form, "
    "with no other text and no unknown keys."
)
_PROVIDER_CAUSES = {
    "refusal": ICause.PROVIDER_REFUSAL,
    "replay_miss": ICause.REPLAY_MISS,
    "no_credentials": ICause.NO_CREDENTIALS,
}


class Investigator:
    def __init__(
        self,
        provider: Provider,
        store: InvestigationStore,
        reader: PinnedReader,
        run_id: str,
        budget: Budget = BUDGET,
        recording: Recording | None = None,
    ) -> None:
        self._provider = provider
        self._store = store
        self._reader = reader
        self._run_id = run_id
        self._budget = budget
        self._recording = recording

    def _lookup(self, key: str) -> LLMResponse | None:
        """The answer already known for this exact request. In a replay the supplied recording is the only authority (a
        previous scan's answers in the same database must neither fill a gap nor override it); otherwise the stored
        answer of an earlier attempt, so a restarted worker never pays twice."""
        if self._recording is not None:
            return self._recording.get(key)
        return self._store.get_response(key)

    def investigate(self, c: Candidate) -> Result:
        if c.prefilter_failure is not None:
            cause, detail = c.prefilter_failure
            return failure_result(c, cause, detail)
        shas: dict[str, str] = {}
        for repo in c.repos:
            sha = self._reader.sha(repo)
            if sha is None:
                return failure_result(c, ICause.EVIDENCE_MISSING, f"repository {repo} has no pinned revision")
            shas[repo] = sha
        observations: list[Observation] = []
        for e in c.evidence:
            observations.extend(scan_instruction_like(e.quote, e.repo, e.path, e.line_start))
        messages: list[tuple[str, str]] = [("user", opening_message(c, shas))]
        keys: list[str] = []
        digests: list[str] = []
        tool_seq = malformed = transient = 0
        while True:
            request = LLMRequest(MODEL, SYSTEM_PROMPT, tuple(messages), self._budget.max_tokens_per_call)
            cached = self._lookup(request.key)
            reserved, attempt = self._store.reserve_call(self._run_id, c.key, request, cached is not None)
            if reserved.startswith("denied"):
                return failure_result(
                    c, ICause.BUDGET, f"budget exhausted ({reserved.split(':', 1)[1]})", observations, keys, digests
                )
            if cached is not None:
                response, replayed = cached, True
                if self._recording is not None:  # keep the replayed answer so the run can be resumed from the store
                    self._store.put_response(request, response, replace=True)
            else:
                try:
                    response, replayed = self._provider.complete(request), False
                except ProviderError as exc:
                    self._store.release_call(self._run_id, c.key, request.key, attempt)
                    if exc.transient and transient < self._budget.max_transient_retries:
                        transient += 1
                        continue
                    return failure_result(
                        c,
                        _PROVIDER_CAUSES.get(exc.kind, ICause.PROVIDER_ERROR),
                        f"provider error: {exc.kind}",
                        observations,
                        keys,
                        digests,
                    )
                self._store.put_response(request, response)
            self._store.settle_call(self._run_id, c.key, request.key, attempt, response, replayed)
            keys.append(request.key)
            digests.append(response_digest(request.key, response))
            try:
                reply = parse_reply(response.text)
            except Malformed as exc:
                if malformed < self._budget.max_malformed_retries:
                    malformed += 1
                    messages += [("assistant", response.text[:2000]), ("user", _REPAIR.format(why=str(exc)[:200]))]
                    continue
                return failure_result(
                    c, ICause.MALFORMED, f"malformed output after one retry: {exc}", observations, keys, digests
                )
            if isinstance(reply, ToolCall):
                if self._store.reserve_tool(self._run_id, c.key, tool_seq) == "denied":
                    return failure_result(
                        c, ICause.BUDGET, "budget exhausted (tool_calls)", observations, keys, digests
                    )
                tool_seq += 1
                shown = run_tool(self._reader, reply)
                observations.extend(shown.observations)
                messages += [("assistant", response.text), ("user", shown.text)]
                continue
            checks = check_quotes(reply.quotes, self._reader)
            return decide(c, reply, checks, _dedupe(observations), keys, digests)


def _dedupe(obs: list[Observation]) -> list[Observation]:
    seen: set[tuple[str, str, int | None, str]] = set()
    out = []
    for o in obs:
        k = (o.repo, o.path, o.line, o.pattern)
        if k not in seen:
            seen.add(k)
            out.append(o)
    return out
