"""Secret management and redaction utilities.

Credentials come from the process environment. The Vault client here is the
optional path, reached only when `PLATFORM_VAULT_ENABLED` is set.
"""

from __future__ import annotations
