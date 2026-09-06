"""Keep provider credentials out of error text that reaches a client.

httpx puts the full request URL in its exception message, and OneMap and
data.gov.sg both take their credential as a query parameter. Interpolating such
an exception into a domain error would publish the token in the 503 body.
"""

from __future__ import annotations

import re

_SENSITIVE_QUERY_PARAM = re.compile(
    r"([?&](?:token|api[_-]?key|account[_-]?key|key|access[_-]?token|secret)=)[^&\s'\"]+",
    re.IGNORECASE,
)

REDACTED = "[redacted]"


def redact_secrets(text: str) -> str:
    """Replace credential query-parameter values, leaving the rest readable."""
    return _SENSITIVE_QUERY_PARAM.sub(rf"\1{REDACTED}", text)
