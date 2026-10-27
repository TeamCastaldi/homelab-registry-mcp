"""OAuth 2.1 resource server for `/mcp` (ADR-024).

The server never issues tokens: Authentik does. This package checks the access
token a client presents (`verifier.py`), answers a valid token outside the
required group with 403, and serves the protected-resource metadata that tells
a client where to sign in. The SDK's own `RequireAuthMiddleware` sends the 401.
"""
