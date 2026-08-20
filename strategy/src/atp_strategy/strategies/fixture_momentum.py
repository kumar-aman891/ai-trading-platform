"""Reference strategy (Milestone 2C) - proves the `Strategy` Protocol and
runner framework end to end. **Not** a trading strategy intended to make
meaningful financial decisions: its "signal" is a fixed, deterministic
threshold against `atp_strategy.context`'s synthetic `FIXTURE_SYNTHETIC`
quotes, chosen only to exercise every layer of the pipeline (context ->
evaluate -> proposal -> persistence) with a nontrivial but fully
reproducible output.

`evaluate()` reads only its `StrategyContext` argument - no I/O, no
`random` module, no `datetime.now()`/`time.time()`, no
`atp_domain.intents` or `atp_domain.risk.engine` import, and no database
or broker/LLM import of any kind (mechanically proven by
`tests/safety/test_no_execution_path_in_strategy.py`).
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from atp_domain.money import Quantity
from atp_domain.strategy import ProposedTrade, StrategyContext
from atp_domain.types import OrderType, Product, Side

STRATEGY_KEY = "fixture-momentum-v1"
STRATEGY_VERSION = 1

#: A quote at or above this price counts as the (entirely synthetic)
#: "signal". Fixed, not configurable - this is a reference strategy, not
#: a tunable one.
_SIGNAL_PRICE_THRESHOLD = Decimal("1")


class FixtureMomentumStrategy:
    """Deterministic reference strategy: proposes a one-lot MARKET BUY for
    every instrument whose synthetic quote price is at or above
    `_SIGNAL_PRICE_THRESHOLD` - trivially true for every quote
    `atp_strategy.context`'s synthetic generator ever produces (a
    tick_size-derived price is always strictly positive). That is
    deliberate: this milestone exists to prove every strategy proposes
    deterministically and the runner/persistence pipeline behaves
    correctly, not to model a real trading signal.
    """

    @property
    def strategy_key(self) -> str:
        return STRATEGY_KEY

    @property
    def strategy_version(self) -> int:
        return STRATEGY_VERSION

    def evaluate(self, context: StrategyContext) -> Sequence[ProposedTrade]:
        proposals: list[ProposedTrade] = []
        for instrument_id in sorted(context.instruments):
            instrument = context.instruments[instrument_id]
            quote = context.quotes.get(instrument_id)
            if quote is None:
                continue
            if quote.last_price.value < _SIGNAL_PRICE_THRESHOLD:
                continue
            proposals.append(
                ProposedTrade(
                    instrument_id=instrument_id,
                    side=Side.BUY,
                    quantity=Quantity(Decimal(instrument.lot_size)),
                    order_type=OrderType.MARKET,
                    limit_price=None,
                    product=Product.CNC,
                )
            )
        return proposals


#: The single instance registered by `atp_strategy.registry` - a strategy
#: is stateless (every field it exposes is a constant), so one shared
#: instance is sufficient and mirrors how `atp_domain.risk.catalog`
#: registers plain rule instances rather than constructing one per call.
DEFAULT_STRATEGY = FixtureMomentumStrategy()
