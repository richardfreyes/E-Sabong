"""Edge guard: abuse and flood protection that wraps the API without touching business handlers.

Everything here is configured with ``SECURITY_*`` variables (app/security/config.py), can be
switched off with ``SECURITY_GUARD_MODE=off``, and fails open: if Redis is slow or down, requests
are served exactly as before. Runbook: docs/security/README.md.
"""
