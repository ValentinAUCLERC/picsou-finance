# Portfolio analytics

> Last updated: 2026-09-21

## Context

The analytics workspace turns existing account history and investment transactions into
decision-support views: portfolio performance, FIRE projection, detailed purchase lines,
target allocation, and a controlled historical backfill. It is read-only with respect to
brokerage accounts: no trade is placed from Picsou.

## How it works

`AnalyticsController` exposes member-scoped endpoints under `/api/analytics`. The frontend
selects the accounts to include; investment accounts are selected by default.

### Performance and heatmap

The performance series forward-fills each selected account's `balance_snapshot` rows. Its
time-weighted return (TWR) removes the effect of net changes in `invested_amount`, so a
deposit does not look like investment performance. The configurable benchmark is a Yahoo
ticker (`^FCHI` for CAC 40 by default) whose EUR historical prices are backfilled through
the existing price service. Monthly TWR values feed the heatmap.

Each `BalanceSnapshot` has an `origin`:

- `OBSERVED` — written by a sync, a manual snapshot or the daily scheduler;
- `RECONSTRUCTED` — rebuilt from imported transactions and historical prices;
- `ESTIMATED` — based on a user-supplied start date for an otherwise undated holding.

The API never overwrites an existing snapshot during a backfill. The UI displays a warning
whenever a selected period includes reconstructed or estimated data.

### FIRE

FIRE settings are stored per family member. The projection uses either the observed,
annualized TWR of the selected portfolio or a manual annual return rate. The FIRE target is
annual expenses divided by the safe withdrawal rate; monthly saving is compounded monthly
until that target is reached.

### Purchase lines and sales

A `BUY` transaction opens a line. A `SELL` reduces every open line for the same ticker in
proportion to its remaining quantity. This intentionally mirrors Picsou's existing
moving-average cost accounting, rather than silently switching the tax/P&L interpretation to
FIFO. A provider holding without transaction history is rendered as one explicitly marked
aggregate line.

### Rebalancing

Targets are saved per member, account and ticker. They must total 100% for the submitted
selection. The service compares current EUR values with those targets and returns suggested
BUY, SELL or HOLD quantities; it never calls a broker API.

### Backfill rule

For positions with transactions, a ticker enters the reconstructed valuation on its first
`BUY` date. For a current holding without an imported purchase, the UI requires a separate
line date (`accountId:TICKER`). That prevents a holding bought in March from appearing in the
portfolio history in January simply because another holding existed then. Historical price
queries are limited to five years, matching the provider's supported range.

## Key files

- `backend/src/main/java/com/picsou/controller/AnalyticsController.java` — REST endpoints.
- `backend/src/main/java/com/picsou/service/AnalyticsService.java` — calculations and backfill.
- `backend/src/main/resources/db/migration/V89__analytics_and_snapshot_origins.sql` — schema.
- `frontend/src/pages/analytics/AnalyticsPage.tsx` — analytics workspace.

## Tests

- `AnalyticsServiceTest` verifies that a partial sale is allocated proportionally across
  purchase lines, preserving average-cost accounting.
