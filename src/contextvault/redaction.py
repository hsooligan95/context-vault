"""Write-time screening: secret / PII patterns are refused at the door.

ContextVault is shared memory for agents. Anything written there is, by
design, readable by every other agent on the system -- which makes it
exactly the wrong place for a credential or a piece of PII to land. So
screening happens on WRITE, before anything reaches the store: a value
whose serialized form matches a known secret/PII pattern is refused,
nothing is persisted, and the refusal names the pattern classes that
fired -- never the matched text itself, so the refusal record cannot
become a second leak.

The pattern set is deliberately small and high-precision (the same
philosophy as agent-gate's screening): cloud access keys, token shapes,
private-key blocks, bearer headers, secret-looking assignments, SSNs.
It is a guardrail against accidents, not a DLP product -- see the
README's Limitations.
"""

from __future__ import annotations

import json
import re

# (pattern name, compiled regex). Names are what a refusal reports.
PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("aws_access_key_id", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("github_token", re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})")),
    ("private_key_block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("bearer_token", re.compile(r"(?i)Bearer\s+[A-Za-z0-9._\-]{16,}")),
    ("secret_assignment", re.compile(
        r"(?i)\b(?:api[_-]?key|secret|password|passwd|access[_-]?token|auth[_-]?token)\b"
        r"\s*[:=]\s*['\"]?[^\s'\"]{8,}")),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
)


class RedactionError(ValueError):
    """A write was refused because its value matched secret/PII patterns.

    Carries only the pattern NAMES that fired -- never the matched text.
    """

    def __init__(self, key: str, patterns: list[str]):
        self.key = key
        self.patterns = patterns
        super().__init__(
            f"write to {key!r} refused: value matches "
            f"{', '.join(patterns)} pattern(s); nothing was stored"
        )


def screen(key: str, value) -> None:
    """Raise RedactionError if `value` matches any screened pattern.

    The value is screened in its JSON-serialized form, so secrets nested
    inside dicts/lists are caught, not just top-level strings.
    """
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    fired = [name for name, rx in PATTERNS if rx.search(text)]
    if fired:
        raise RedactionError(key, fired)
