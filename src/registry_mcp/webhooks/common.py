"""Request helpers shared by the inbound HTTP receivers in this package."""

from __future__ import annotations

from pydantic import ValidationError
from starlette.requests import Request


async def read_capped(request: Request, limit: int) -> bytes | None:
    """The request body, or None as soon as it passes `limit` bytes.

    Reads the stream chunk by chunk and stops at the cap, so a body with no
    Content-Length (chunked) or a false one is never buffered in full first.
    """
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def declared_too_large(request: Request, limit: int) -> bool:
    """Whether the request's own Content-Length already exceeds `limit`."""
    declared = request.headers.get("content-length")
    return bool(declared and declared.isdigit() and int(declared) > limit)


def validation_detail(exc: ValidationError) -> list[dict[str, str]]:
    """Project a ValidationError into JSON-serializable detail.

    `ValidationError.errors()` can carry a `ctx` holding the original exception
    object, which `JSONResponse` cannot encode — serializing it raw would turn
    the 422 path into a 500.
    """
    detail = []
    for err in exc.errors():
        detail.append(
            {
                "loc": ".".join(str(part) for part in err.get("loc", ())),
                "msg": str(err.get("msg", "")),
                "type": str(err.get("type", "")),
            }
        )
    return detail
