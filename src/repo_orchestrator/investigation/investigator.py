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
from .provider import LLMRequest, LLMResponse, Provider, ProviderError
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
        self, provider: Provider, store: InvestigationStore, reader: PinnedReader, run_id: str, budget: Budget = BUDGET
    ) -> None:
        self._provider = provider
        self._store = store
        self._reader = reader
        self._run_id = run_id
        self._budget = budget

    def _complete(self, request: LLMRequest) -> tuple[LLMResponse, bool]:
        """Recorded answer first (a restarted worker, an imported recording), the provider otherwise; live answers are
        recorded immediately so a crash after the call never pays twice."""
        recorded = self._store.get_response(request.key)
        if recorded is not None:
            return recorded, True
        response = self._provider.complete(request)
        self._store.put_response(request, response)
        return response, False

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
        tool_seq = malformed = transient = 0
        tries: dict[str, int] = {}  # attempts per exact request: a transient retry re-sends the same request
        while True:
            request = LLMRequest(MODEL, SYSTEM_PROMPT, tuple(messages), self._budget.max_tokens_per_call)
            attempt = tries.get(request.key, 0)
            reserved = self._store.reserve_call(self._run_id, c.key, request, attempt)
            if reserved.startswith("denied"):
                return failure_result(
                    c, ICause.BUDGET, f"budget exhausted ({reserved.split(':', 1)[1]})", observations, keys
                )
            try:
                response, replayed = self._complete(request)
            except ProviderError as exc:
                self._store.release_call(self._run_id, c.key, request.key, attempt)
                tries[request.key] = attempt + 1
                if exc.transient and transient < self._budget.max_transient_retries:
                    transient += 1
                    continue
                return failure_result(
                    c,
                    _PROVIDER_CAUSES.get(exc.kind, ICause.PROVIDER_ERROR),
                    f"provider error: {exc.kind}",
                    observations,
                    keys,
                )
            self._store.settle_call(self._run_id, c.key, request.key, attempt, response, replayed)
            keys.append(request.key)
            try:
                reply = parse_reply(response.text)
            except Malformed as exc:
                if malformed < self._budget.max_malformed_retries:
                    malformed += 1
                    messages += [("assistant", response.text[:2000]), ("user", _REPAIR.format(why=str(exc)[:200]))]
                    continue
                return failure_result(
                    c, ICause.MALFORMED, f"malformed output after one retry: {exc}", observations, keys
                )
            if isinstance(reply, ToolCall):
                if self._store.reserve_tool(self._run_id, c.key, tool_seq) == "denied":
                    return failure_result(c, ICause.BUDGET, "budget exhausted (tool_calls)", observations, keys)
                tool_seq += 1
                shown = run_tool(self._reader, reply)
                observations.extend(shown.observations)
                messages += [("assistant", response.text), ("user", shown.text)]
                continue
            checks = check_quotes(reply.quotes, self._reader)
            return decide(c, reply, checks, _dedupe(observations), keys)


def _dedupe(obs: list[Observation]) -> list[Observation]:
    seen: set[tuple[str, str, int | None, str]] = set()
    out = []
    for o in obs:
        k = (o.repo, o.path, o.line, o.pattern)
        if k not in seen:
            seen.add(k)
            out.append(o)
    return out
