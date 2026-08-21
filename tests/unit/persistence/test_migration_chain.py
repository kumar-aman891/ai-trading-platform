"""Alembic migration-chain sanity, no database connection required.

Uses Alembic's `ScriptDirectory` to load and validate every revision file
under persistence/src/atp_persistence/migrations/versions/ - this catches a
broken revision graph (multiple heads, a dangling `down_revision`, a
migration file that fails to import) without needing Postgres, so it runs
in every environment.
"""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

_PERSISTENCE_DIR = Path(__file__).resolve().parents[3] / "persistence"
_ALEMBIC_INI = _PERSISTENCE_DIR / "alembic.ini"


def _script_directory() -> ScriptDirectory:
    config = Config(str(_ALEMBIC_INI))
    config.set_main_option(
        "script_location", str(_PERSISTENCE_DIR / "src/atp_persistence/migrations")
    )
    return ScriptDirectory.from_config(config)


def test_alembic_ini_and_migrations_directory_exist() -> None:
    assert _ALEMBIC_INI.is_file()
    assert (_PERSISTENCE_DIR / "src/atp_persistence/migrations/versions").is_dir()


def test_migration_chain_has_exactly_one_head() -> None:
    script = _script_directory()
    heads = script.get_heads()
    assert len(heads) == 1, f"expected a single linear head, found {heads}"
    assert heads[0] == "0006_strategy_attribution"


def test_migration_chain_is_linear_from_base_to_head() -> None:
    script = _script_directory()
    revisions = list(script.walk_revisions())
    assert [r.revision for r in revisions] == [
        "0006_strategy_attribution",
        "0005_job_queue_claim_constraints",
        "0004_paper_cash_ledger_seed",
        "0003_table_grants",
        "0002_seed_fixture_instruments",
        "0001_core_audit_paper_schema",
    ]
    # Every revision but the base has exactly one down_revision - no
    # branch point exists in this chain.
    for r in revisions[:-1]:
        assert isinstance(r.down_revision, str)
    assert revisions[-1].down_revision is None


def test_every_migration_module_defines_upgrade_and_downgrade() -> None:
    script = _script_directory()
    for revision in script.walk_revisions():
        module = revision.module
        assert callable(module.upgrade)
        assert callable(module.downgrade)


#: Alembic's own `alembic_version.version_num` column is
#: `VARCHAR(32)` - it creates the table itself, so this is not a length
#: this repository chooses or can widen from a model definition.
_ALEMBIC_VERSION_NUM_MAX_LENGTH = 32


def test_every_revision_id_fits_alembic_version_num_column() -> None:
    """Regression guard for a real defect found by CI (Milestone 2D): the
    revision id `0006_strategy_proposal_attribution` was 34 characters, so
    `alembic upgrade head` applied migration 0006's DDL and then failed
    stamping it with `StringDataRightTruncation: value too long for type
    character varying(32)`. Because Alembic runs the migration and its
    stamp in one transaction, the whole upgrade rolled back and *every*
    Docker-gated integration test that depends on the `migrated_database`
    fixture errored - 92 errors from one over-long identifier.

    Nothing else catches this: the id is a plain string with no schema
    behind it, the naming convention is `NNNN_snake_case_description`
    which invites long descriptions, and `0005_job_queue_claim_constraints`
    already sat at exactly 32. Asserted here rather than in an integration
    test so it fails in milliseconds, with no database, before CI."""
    script = _script_directory()
    for revision in script.walk_revisions():
        assert len(revision.revision) <= _ALEMBIC_VERSION_NUM_MAX_LENGTH, (
            f"revision id {revision.revision!r} is {len(revision.revision)} characters; "
            f"alembic_version.version_num is VARCHAR({_ALEMBIC_VERSION_NUM_MAX_LENGTH}), "
            f"so `alembic upgrade` would roll back after applying this migration's DDL"
        )
