"""Process entrypoint: `uvicorn atp_api.main:app`, or `python -m atp_api.main`
for local development.

The single, visible construction site for the process-wide `app` object -
`atp_api.app.create_app()` itself never runs at import time anywhere else
in this package (no other module constructs a `Settings`, an engine, or an
`app` at module scope), so this is the only place a "hidden global" could
even arise, and it does not: every value `create_app` receives is built
right here, explicitly, from `load_settings()`/`load_api_settings()`.
"""

from __future__ import annotations

import os

from atp_api.app import create_app
from atp_api.config import load_api_settings
from atp_platform.config import load_settings
from atp_platform.logging import configure_logging

settings = load_settings()
configure_logging(service="atp-api", level=settings.log_level)

app = create_app(settings=settings, api_settings=load_api_settings())


def run() -> None:
    import uvicorn

    host = os.environ.get("ATP_API_HOST", "127.0.0.1")
    port = int(os.environ.get("ATP_API_PORT", "8000"))
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run()
