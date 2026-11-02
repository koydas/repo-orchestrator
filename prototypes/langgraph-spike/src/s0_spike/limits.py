"""Hard limits. Application constants, enforced in code; never read from run input or from a prompt."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Limits:
    max_candidates: int = 10  # candidates accepted per run; the rest are recorded as not investigated
    max_attempts: int = 2  # investigation attempts per candidate
    max_investigations: int = 15  # agent invocations per run (attempt spends), all candidates together
    max_steps: int = 60  # node executions per run, workers included (application-level step budget)
    max_units_per_investigation: int = 5  # abstract tool-call units one investigation may spend
    recursion_limit: int = 100  # LangGraph's own backstop, passed in the run config


DEFAULT_LIMITS = Limits()
# Steps kept back for collect, aggregate, approval_gate and a finalize node once work is denied.
STEP_RESERVE = 4
