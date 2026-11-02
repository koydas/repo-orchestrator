class SpikeError(Exception):
    """Base class of application errors raised by the spike."""


class InvalidRunRequest(SpikeError):
    pass


class RunExists(SpikeError):
    pass


class RunNotFound(SpikeError):
    pass


class RunAlreadyFinalized(SpikeError):
    pass


class NotAwaitingApproval(SpikeError):
    pass


class RunAlreadyInitialized(SpikeError):
    """Raised inside the graph when a thread that already has state receives a start input."""


class FrozenInputTampered(SpikeError):
    pass


class ReducerConflict(SpikeError):
    pass
