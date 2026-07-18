"""Typed errors for the CodeSmriti API client.

Callers branch on these instead of inspecting HTTP status codes. Each maps to a
distinct CLI exit code in the `smriti` CLI presenter.
"""


class SmritiError(Exception):
    """Base error for any failed CodeSmriti API call."""


class SmritiConfigError(SmritiError):
    """Missing or invalid configuration (e.g. no token set)."""


class SmritiAuthError(SmritiError):
    """The token was rejected (HTTP 401)."""


class SmritiNotFoundError(SmritiError):
    """The requested resource does not exist (HTTP 404)."""
