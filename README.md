# TickerRisk MCP Server

**Check an options trade for hidden catalysts before you sell premium — from inside Claude, ChatGPT, or Cursor.**

An [MCP](https://modelcontextprotocol.io) server that lets an AI assistant answer questions like
*"is it safe to sell a 30-day put on HPE?"* with real event data instead of a guess.

The problem it solves: a fat option premium is usually the market pricing in a **known upcoming
event** — an earnings report, an FDA decision, a court date — not free money. Some screeners
flag earnings; few check the wider event calendar. This one scores every candidate across
earnings, FDA, legal, SEC and clinical events landing *inside the expiry window*, and filters
out the traps.

```
You:    Is it safe to sell a 4-week put on INTC?

Claude: [calls scan_ticker]
        INTC scores 100/100 (HIGH) over a 4-week window. Earnings land in 4 days,
        inside your expiry. The premium is pricing that gap — 279% annualized on
        the $88 put is compensation for event risk, not an edge.
```

## Why this is different

Risk is **horizon-dependent**, and that is the whole point. The same stock:

| Ticker | 1-week window | 4-week window | Why it changes |
|---|---|---|---|
| AAPL | 51 (MEDIUM) | 97 (HIGH) | Earnings sit in week 3 |
| KO | 22 (LOW) | 62 (MEDIUM) | Earnings enter the window |

Sell a weekly and you are fine. Sell a monthly on the same ticker and you have sold
straight through an earnings report. A screener that shows one number cannot tell you that.

## Install

### Claude Desktop

Add to your `claude_desktop_config.json`:

**macOS/Linux** — `~/Library/Application Support/Claude/claude_desktop_config.json`
**Windows** — `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "tickerrisk": {
      "command": "uvx",
      "args": ["tickerrisk-mcp"]
    }
  }
}
```

Restart Claude Desktop. You should see the TickerRisk tools in the tool menu.

### Claude Code

```bash
claude mcp add tickerrisk -- uvx tickerrisk-mcp
```

### Cursor

In `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "tickerrisk": { "command": "uvx", "args": ["tickerrisk-mcp"] }
  }
}
```

### From source

```bash
git clone https://github.com/Tickerrisk/tickerrisk-mcp
cd tickerrisk-mcp
pip install -e .
tickerrisk-mcp
```

## Tools

| Tool | What it answers |
|---|---|
| `scan_ticker` | "How risky is selling an option on X over the next N weeks?" |
| `find_wheel_candidates` | "What cash-secured puts can I sell this week without an earnings trap?" |
| `find_covered_calls` | "What calls can I sell against shares I already own?" |
| `compare_tickers` | "Which of these stocks is safest to sell premium on right now?" |

### `scan_ticker(ticker, expiry_weeks=4)`

Returns a 0–100 catalyst-risk score (higher = riskier) with the events driving it:
earnings date and whether it falls in the window, FDA/clinical milestones, legal filings,
SEC events, implied volatility, IV Rank, and the expected move.

Bands: **≥70 HIGH · 45–69 MEDIUM · <45 LOW**

### `find_wheel_candidates(week, risk, max_risk, min_put_oi, sector, limit)`

Scans the S&P 500 for cash-secured puts and returns only names whose catalyst score over
the option's own expiry window is under `max_risk`. Flags any candidate whose earnings land
before expiry.

### `find_covered_calls(week, risk, max_risk, min_call_oi, sector, limit)`

Same gating for the call side. Income is computed from **time value only**, so in-the-money
strikes do not show inflated yields.

### `compare_tickers(tickers, expiry_weeks=4)`

Side-by-side catalyst risk for up to 25 symbols on one horizon.

## Access and authentication

No signup needed to start. Access follows tickerrisk.com's normal model:

- **First 24 hours** — full access, no account, keyed to your IP
- **After that** — a free account at [tickerrisk.com](https://tickerrisk.com) adds 14 days
- The strategy scanners (`find_wheel_candidates`, `find_covered_calls`, `compare_tickers`)
  read cached data and stay available

To authenticate an existing account, set a token:

```json
{
  "mcpServers": {
    "tickerrisk": {
      "command": "uvx",
      "args": ["tickerrisk-mcp"],
      "env": { "TICKERRISK_TOKEN": "your-jwt-here" }
    }
  }
}
```

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `TICKERRISK_TOKEN` | *(none)* | JWT for an existing account |
| `TICKERRISK_BASE_URL` | `https://tickerrisk.com` | Override the API host |
| `TICKERRISK_TIMEOUT` | `45` | Request timeout in seconds |

## Data and limitations

Being straight about what this is and is not:

- **Catalyst data** — earnings dates, SEC filings, court records (CourtListener),
  ClinicalTrials.gov, and news. Public sources, so incomplete or delayed entries happen.
- **Option premiums are indicative, not live NBBO.** Quotes are roughly 15 minutes delayed
  and estimated where no bid exists (marked in the output). Always confirm in your broker
  before trading.
- **Outside US market hours** (9:30–16:00 ET) quotes go stale and candidate lists thin out.
  This is upstream data reality, not a bug.
- **The score is not a prediction.** It measures *scheduled event exposure* and volatility.
  A LOW score does not mean a stock cannot drop; it means no known catalyst was found in
  that window.

Not financial advice. For research only.

## How it compares

Being accurate about this, because the differentiator is narrower than most tools claim:

Earnings-date checking is **not** unique. Barchart's options screener has a "Flag Earnings"
option that marks contracts whose next earnings date falls on or before expiration.
Market Chameleon tracks biotech catalysts and links them to option chains. If earnings
alone is what you need, those are mature tools with real-time data and far more filters —
use them.

What this tool does differently is combine **five** event types — earnings, FDA decisions,
legal filings, SEC events and clinical milestones — into a single 0–100 score tied to your
expiry window, and filter on it by default rather than showing an optional flag column.
Court records as an options-risk input in particular is something we have not found
elsewhere.

So: if you want the deepest screener, use Barchart or Option Samurai. If you want one
number that answers "is there anything scheduled inside this expiry", that is what this is.

## Links

- Website — [tickerrisk.com](https://tickerrisk.com)
- Wheel scanner — [tickerrisk.com/wheel](https://tickerrisk.com/wheel)
- Covered calls — [tickerrisk.com/covered-calls](https://tickerrisk.com/covered-calls)

## License

MIT

---

<!-- Ownership marker required by the official MCP Registry: it verifies control of the
     PyPI package by checking this exact string appears in the published README. Must match
     the "name" field in server.json. Do not remove or reword. -->

mcp-name: io.github.PasiutusVovere/tickerrisk-mcp
