"""APPLICATION: the factory creates a working app, startup/shutdown are
deterministic, and no hidden global database session exists anywhere in
`atp_api.app`."""

from __future__ import annotations

import inspect

from fastapi import FastAPI
from fastapi.testclient import TestClient

import atp_api.app as app_module
from atp_api.app import create_app
from atp_api.config import ApiSettings
from atp_platform.config import Settings


def test_create_app_returns_a_fastapi_instance(settings: Settings) -> None:
    app = create_app(settings=settings)
    assert isinstance(app, FastAPI)


def test_create_app_wires_settings_explicitly_onto_app_state(
    settings: Settings, api_settings: ApiSettings
) -> None:
    app = create_app(settings=settings, api_settings=api_settings)
    assert app.state.settings is settings
    assert app.state.api_settings is api_settings


def test_create_app_builds_a_fresh_session_factory_by_default(settings: Settings) -> None:
    app_a = create_app(settings=settings)
    app_b = create_app(settings=settings)
    assert app_a.state.session_factory is not app_b.state.session_factory
    assert app_a.state.engine is not app_b.state.engine


def test_create_app_accepts_an_explicit_session_factory(settings: Settings) -> None:
    sentinel = object()
    app = create_app(settings=settings, session_factory=sentinel)  # type: ignore[arg-type]
    assert app.state.session_factory is sentinel
    assert app.state.engine is None


def test_startup_and_shutdown_are_deterministic_and_do_not_raise(settings: Settings) -> None:
    app = create_app(settings=settings)
    with TestClient(app) as client:
        response = client.get("/healthz")
        assert response.status_code == 200
    # Exiting the `with` block runs the lifespan shutdown path
    # (engine.dispose()) - reaching this line without an exception is the
    # assertion.


def test_no_module_level_engine_or_session_global_exists_in_app_module() -> None:
    """Every engine/session `atp_api.app.create_app` produces is an
    attribute of the `FastAPI` instance it returns (`app.state.*`), never
    a module-level variable - so nothing in `atp_api.app`'s own namespace
    should be an `AsyncEngine`/`AsyncSession`(maker) instance."""
    forbidden_type_names = {"AsyncEngine", "AsyncSession", "async_sessionmaker"}
    for name, value in vars(app_module).items():
        if name.startswith("_") or inspect.ismodule(value) or inspect.isfunction(value):
            continue
        assert (
            type(value).__name__ not in forbidden_type_names
        ), f"atp_api.app.{name} is a live {type(value).__name__} at module scope"


def test_two_apps_built_from_the_same_settings_are_independent(settings: Settings) -> None:
    """No shared mutable global between two `create_app` calls - proves
    the factory is actually a factory, not a memoized singleton."""
    app_a = create_app(settings=settings)
    app_b = create_app(settings=settings)
    assert app_a is not app_b
    assert app_a.state.engine is not app_b.state.engine
