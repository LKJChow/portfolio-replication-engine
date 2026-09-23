# Institutional Portfolio Replication Engine

Reconstructs the disclosed long-equity portfolios of institutional managers from
SEC Form 13F filings, estimates their factor exposures, and backtests what an
outside investor could actually have captured by following them.

**Status:** in development. Ingestion and parsing are complete and tested;
pricing, factor estimation and the API are in progress.

---

## What 13F is, and what it is not

This project is built on a disclosure with hard limits, and the analysis is
designed around them rather than in spite of them.

| Limit | Consequence |
|---|---|
| Filed quarterly, due **45 days** after quarter end | The earliest anyone outside can act is 45 days late — up to ~135 days after a position was opened early in a quarter |
| Long US-listed equity only | No shorts, no cash, no bonds, no foreign listings, mostly no derivatives |
| Managers over $100M in qualifying assets | Smaller and non-US managers do not appear at all |

So this replicates a manager's **disclosed long US equity sleeve** — not their
portfolio, and not their returns. A fund running significant shorts, leverage or
macro exposure will look nothing like its 13F.

Rather than disclaim the reporting lag and move on, the engine measures it:
every filing stores both `period_end` and `reported_at`, so "how much of the
return survives the disclosure delay?" is a result the project produces.

## Funds tracked

Chosen so that factor analysis shows genuine contrast:

| Manager | Style | Why included |
|---|---|---|
| Berkshire Hathaway | Concentrated value/quality | Longest clean filing history |
| Pershing Square | Activist | ~8 positions — extreme concentration |
| Tiger Global | Growth / technology | Includes a severe 2022 drawdown |
| Baupost Group | Deep value | Low turnover |
| Renaissance Technologies | Quantitative | Thousands of small positions, high turnover |
| Lone Pine Capital | Growth | Mid-concentration comparison |
| **Bridgewater Associates** | Macro | **Its 13F is mostly ETFs — included to demonstrate the disclosure's limits, not despite them** |

## Architecture

```
backend/
  app/        FastAPI service, settings, ORM models
  ingest/     SEC EDGAR client and 13F parser, price loader
  analysis/   factor regressions, backtest, benchmark comparison
  tests/      offline tests against fixtures
frontend/     React dashboard
```

Python · FastAPI · PostgreSQL · React · Docker

## Parsing details that matter

Three mistakes that quietly produce wrong numbers, each handled and tested:

**The 2023 units change.** 13F values were reported in *thousands* through 2022
and in *whole dollars* from 2023 onward. Mixing the scales inflates every older
portfolio by 1000×.

**Puts are not shorts.** A put line in a 13F is a *long put*. Counting it as
equity exposure inverts the sign of the position.

**Amendments restate.** A `13F-HR/A` supersedes the original for that period;
summing both double-counts the portfolio.

Also handled: filings with no XML namespace, the several filenames used for the
holdings table, and multiple lines per CUSIP split by investment discretion
(which understates concentration if not aggregated).

## Method

Factor exposures are estimated against the Fama–French factors from the Ken
French Data Library. Backtests compare the replicated sleeve to a passive
benchmark, with the disclosure lag applied honestly — positions are only
investable from the filing date, never from the quarter end.

## Running it

```bash
cp .env.example .env          # add your SEC User-Agent
cd backend && pip install -r requirements.txt
pytest tests -q
```

The SEC requires a `User-Agent` containing contact information and rate-limits
to 10 requests/second; the client stays under both.

## License

MIT
