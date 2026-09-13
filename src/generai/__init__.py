"""GenerAI public API client."""

from .client import GenerAI, credits, new_idempotency_key
from .errors import APIError, GenerAIError, JobFailed, PollingTimeout, ProtocolError, TransportError

__version__ = "0.1.0"
__all__ = [
    "GenerAI", "credits", "new_idempotency_key", "GenerAIError", "APIError",
    "TransportError", "ProtocolError", "JobFailed", "PollingTimeout",
]
