"""M3 bounded investigation (C5 Investigator, P7, W3).

The one place where a language model may be called (ADR-0014). Importing this package imports neither the network
client nor LangGraph: ``provider`` and ``workflow`` are imported by the modules that need them (ADR-0014 §1, ADR-0016 §1).
"""
