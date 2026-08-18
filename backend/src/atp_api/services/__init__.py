"""Application services: the layer between routers and repositories.

Routers never talk to a repository, a `Session`, or a Unit of Work
directly - they call a function here, which returns a plain dataclass
("view") that the router then converts to its Pydantic response model.
Every service function here is a plain `async def`, callable and testable
without an HTTP request (FastAPI's `TestClient`/ASGI machinery is not
required to exercise this layer's logic).
"""

from __future__ import annotations
