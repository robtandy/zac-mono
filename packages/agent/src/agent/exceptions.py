class AgentError(Exception):
    """Base exception for agent errors."""


class ProcessNotRunning(AgentError):
    """Raised when attempting to communicate with a pi process that isn't running."""
