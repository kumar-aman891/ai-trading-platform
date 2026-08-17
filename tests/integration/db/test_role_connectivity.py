"""Step 5 database security item (h): connectivity works using the intended,
least-privilege roles - not just the bootstrap superuser."""

from __future__ import annotations

import psycopg


def test_owner_role_can_connect(owner_connection: psycopg.Connection) -> None:
    with owner_connection.cursor() as cur:
        cur.execute("SELECT current_user")
        assert cur.fetchone() == ("atp_owner",)


def test_api_role_can_connect(api_connection: psycopg.Connection) -> None:
    with api_connection.cursor() as cur:
        cur.execute("SELECT current_user")
        assert cur.fetchone() == ("atp_api",)


def test_paper_exec_role_can_connect(paper_exec_connection: psycopg.Connection) -> None:
    with paper_exec_connection.cursor() as cur:
        cur.execute("SELECT current_user")
        assert cur.fetchone() == ("atp_paper_exec",)


def test_worker_role_can_connect(worker_connection: psycopg.Connection) -> None:
    with worker_connection.cursor() as cur:
        cur.execute("SELECT current_user")
        assert cur.fetchone() == ("atp_worker",)
