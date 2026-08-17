# Runtime Agent Model

## Principle

Use a small number of specialist agents instead of one autonomous mega-agent.

## Agents

### Research Orchestrator
Routes stock/strategy questions to specialists and synthesizes evidence.

### Technical Analyst
Calculates indicators, patterns, trend/regime measures and multi-timeframe context.

### Fundamental Analyst
Analyzes earnings, valuation, profitability, leverage, cash flow, growth and corporate events.

### News/Event Analyst
Finds and classifies current catalysts, filings, corporate announcements and market-moving events.

### Portfolio/Risk Analyst
Evaluates exposure, concentration, correlation, drawdown, liquidity and proposed risk.

### Strategy Researcher
Converts a trading hypothesis into a versioned, testable strategy specification.

### Backtest Auditor
Looks for statistical and implementation mistakes rather than optimizing returns.

### Execution Planner
Creates a typed order proposal only. It cannot execute.

### Risk Gate
Prefer deterministic code, not an LLM. It returns PASS/REJECT with explicit rule IDs.

### Execution Gateway
Deterministic service with broker credentials and the only service permitted to send live orders.

## Agent communication

Use typed artifacts rather than long natural-language chains:
- ResearchFinding
- SignalObservation
- StrategySpec
- TradeProposal
- RiskDecision
- ExecutionResult
- ReconciliationResult

Store references to evidence rather than duplicating full payloads.

## Parallelization policy

Parallelize independent research tasks: technical, fundamental, news, risk, comparable assets.
Do not parallelize conflicting writes to orders, positions, risk state, or account state.

## Token budget policy

Use cheap/non-LLM computation for indicators, screens, portfolio math and backtest metrics. Use the LLM for synthesis, interpretation, hypothesis generation, and explanations.
Cache repeated research. Summarize before passing data to a synthesis agent.
