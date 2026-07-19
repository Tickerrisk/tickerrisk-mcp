"""TickerRisk MCP server.

Exposes TickerRisk's catalyst-risk scanner as MCP tools so an AI assistant can
answer "is it safe to sell this option?" with real event data instead of a guess.

What it adds over a generic options screener: every candidate is scored across FIVE
event types at once — earnings, FDA decisions, legal filings, SEC events and clinical
milestones — landing INSIDE the expiry window, as a single 0-100 number that results
are filtered on. (Earnings-only flags exist elsewhere, e.g. Barchart's "Flag Earnings";
the combined score and the legal/SEC inputs are the part that is hard to find.)
A fat premium is usually the market pricing an event, not free money.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

BASE_URL = os.environ.get("TICKERRISK_BASE_URL", "https://tickerrisk.com").rstrip("/")
TOKEN = os.environ.get("TICKERRISK_TOKEN", "").strip()
TIMEOUT = float(os.environ.get("TICKERRISK_TIMEOUT", "45"))

INSTRUCTIONS = """TickerRisk scores US stocks for *catalyst risk* — scheduled or public
events that can gap a stock before an option expires: earnings reports, FDA decisions,
legal filings, SEC events, plus the implied-volatility expected move.

Use it whenever a question involves the risk of an options trade, especially selling
premium (cash-secured puts, covered calls, the wheel). The central idea most option
screeners miss: a rich premium is usually the market pricing in a known upcoming event,
not free money. Risk is also horizon-dependent — always pass the expiry window that
matches the trade being discussed, because a stock can be LOW risk for one week and
HIGH risk for six.

Scores describe scheduled events and volatility, not price direction. Present results
as risk context for the user's own decision, never as a recommendation to trade."""

mcp = FastMCP(
    "tickerrisk",
    instructions=INSTRUCTIONS,
    website_url="https://tickerrisk.com",
)


# ── HTTP plumbing ─────────────────────────────────────────────────────────────

class TickerRiskError(RuntimeError):
    """A user-facing failure that should be reported as text, not a stack trace."""


def _headers() -> dict[str, str]:
    h = {"User-Agent": "tickerrisk-mcp/0.1 (+https://tickerrisk.com)"}
    if TOKEN:
        h["Authorization"] = f"Bearer {TOKEN}"
    return h


async def _get(path: str, params: dict[str, Any] | None = None) -> dict:
    """GET a TickerRisk endpoint, translating gate/quota errors into plain guidance."""
    url = f"{BASE_URL}{path}"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
            r = await client.get(url, params=params or {}, headers=_headers())
    except httpx.TimeoutException:
        raise TickerRiskError(
            "TickerRisk took too long to respond. Uncached tickers are scanned live "
            "and can take ~30s the first time — try again."
        )
    except httpx.HTTPError as exc:
        raise TickerRiskError(f"Could not reach TickerRisk ({exc.__class__.__name__}).")

    if r.status_code == 429:
        raise TickerRiskError(_quota_message(r))
    if r.status_code == 403:
        raise TickerRiskError(
            "This data needs an account. Free access: https://tickerrisk.com — 24 hours "
            "with no signup, then a free account adds 14 days. Set TICKERRISK_TOKEN to "
            "authenticate an existing account."
        )
    if r.status_code >= 400:
        raise TickerRiskError(f"TickerRisk returned HTTP {r.status_code} for {path}.")

    try:
        return r.json()
    except ValueError:
        raise TickerRiskError("TickerRisk returned a malformed response.")


def _quota_message(r: httpx.Response) -> str:
    detail: Any = {}
    try:
        detail = (r.json() or {}).get("detail") or {}
    except ValueError:
        pass
    if isinstance(detail, dict) and detail.get("message"):
        return f"{detail['message']} — https://tickerrisk.com"
    return (
        "Free access has ended for this IP. Create a free account at "
        "https://tickerrisk.com for 14 more days of full access."
    )


# ── Formatting helpers ────────────────────────────────────────────────────────

def _num(v: Any, suffix: str = "", dash: str = "n/a") -> str:
    if v is None:
        return dash
    if isinstance(v, float):
        return f"{v:g}{suffix}"
    return f"{v}{suffix}"


def _money(v: Any) -> str:
    return f"${v:,.2f}" if isinstance(v, (int, float)) else "n/a"


def _band_note(band: str | None, score: Any) -> str:
    """Plain-language reading of the score. Bands: >=70 HIGH, >=45 MEDIUM, <45 LOW."""
    b = (band or "").upper()
    if b == "HIGH":
        return "HIGH — a known catalyst lands inside this window. Selling premium here is a bet on the event, not on time decay."
    if b == "MEDIUM":
        return "MEDIUM — something is in the window worth checking before you commit."
    if b == "LOW":
        return "LOW — no major scheduled catalyst found in this window."
    return f"score {score}"


def _catalyst_lines(components: list[dict]) -> list[str]:
    """The components that actually contributed points, biggest first."""
    live = [c for c in (components or []) if (c.get("points") or 0) > 0]
    live.sort(key=lambda c: -(c.get("points") or 0))
    out = []
    for c in live:
        count = c.get("count")
        cnt = f" ({count})" if isinstance(count, int) and count > 1 else ""
        out.append(f"- {c.get('label', 'Signal')}{cnt}: +{c.get('points')} pts")
    return out


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool()
async def scan_ticker(ticker: str, expiry_weeks: int = 4) -> str:
    """Check how risky it is to sell or buy an option on a stock, over a specific expiry window.

    Use this whenever someone asks whether it is safe to sell a put or a covered
    call on a ticker, whether a premium is "too good", what could gap a stock
    before expiry, or what catalysts are coming up for a company.

    Returns a 0-100 catalyst-risk score (higher = riskier) plus the specific
    events driving it: earnings dates, FDA decisions, legal filings, SEC events,
    and the implied-volatility expected move. The key idea is that risk depends on
    the expiry window — a stock can be LOW risk for a 1-week option and HIGH risk
    for a 6-week option that spans an earnings report.

    Args:
        ticker: Stock symbol, e.g. "AAPL", "HPE", "KO".
        expiry_weeks: How many weeks until the option expires (1-52). Match this to
            the actual trade being considered. Defaults to 4 (~30 DTE, the typical
            premium-selling horizon).
    """
    t = (ticker or "").upper().strip()
    if not t:
        return "Please provide a ticker symbol."
    expiry_weeks = max(1, min(52, int(expiry_weeks)))

    try:
        data = await _get("/api/scan", {"ticker": t, "expiry_weeks": expiry_weeks})
    except TickerRiskError as exc:
        return str(exc)

    company = data.get("company_name") or t
    score = data.get("risk_score")
    band = data.get("risk_band")
    price = data.get("current_price")

    lines = [
        f"# {company} ({t}) — risk over the next {expiry_weeks} week(s)",
        "",
        f"**Catalyst risk score: {_num(score)}/100 ({band or 'n/a'})**",
        _band_note(band, score),
        "",
    ]

    if price is not None:
        lines.append(f"Price: {_money(price)}")

    iv_pct, iv_rank = data.get("iv_pct"), data.get("iv_rank")
    if iv_pct is not None or iv_rank is not None:
        # IV Rank is an index 0-100, NOT a percentage — never suffix it with %.
        lines.append(
            f"Implied volatility: {_num(iv_pct, '%')} · IV Rank: {_num(iv_rank)} "
            f"(how expensive options are vs this stock's own past year)"
        )

    earnings = data.get("earnings") or {}
    if earnings.get("earnings_date"):
        in_win = earnings.get("in_window")
        flag = "INSIDE this window" if in_win else "outside this window"
        lines.append(f"Next earnings: {str(earnings['earnings_date'])[:10]} — {flag}")

    cats = _catalyst_lines(data.get("components") or [])
    if cats:
        lines += ["", "## What is driving the score", *cats]

    if data.get("summary"):
        lines += ["", "## Summary", str(data["summary"])]
    if data.get("recommendation"):
        lines += ["", "## Read", str(data["recommendation"])]

    lines += [
        "",
        "---",
        f"Source: TickerRisk — https://tickerrisk.com/ticker/{t}",
        "Scores reflect scheduled/public events, not price prediction. Not financial advice.",
    ]
    return "\n".join(lines)


@mcp.tool()
async def find_wheel_candidates(
    week: str = "all",
    risk: str = "balanced",
    max_risk: float = 55,
    min_put_oi: int = 100,
    sector: str = "",
    limit: int = 10,
) -> str:
    """Find cash-secured put candidates (the wheel strategy) that have no hidden catalyst in the expiry window.

    Use this when someone asks what puts to sell this week, for wheel or
    cash-secured-put ideas, for "safe premium" to collect, or which stocks pay well
    without an earnings report coming up.

    Scans the whole S&P 500 and returns only names whose catalyst-risk score over
    the option's own expiry window is under max_risk — so candidates with earnings,
    FDA decisions or legal events inside the window are filtered out rather than
    surfaced as fake high-yield opportunities.

    Args:
        week: Expiry bucket — "this" (1-7 days), "next" (8-14), "two", "three",
            "month" (1-35), or "all" (up to 45 days). "all" returns the most results.
        risk: How close to the money to sell — "conservative" (~0.20 delta, further
            out, safer), "balanced" (~0.30), or "aggressive" (~0.40, more premium,
            more assignment risk).
        max_risk: Maximum catalyst-risk score to allow, 0-100. 55 is a sensible
            default; lower it to 40 for a stricter list.
        min_put_oi: Minimum put open interest, to keep results actually tradeable.
            Keep at 100 during market hours.
        sector: Optional GICS sector filter, e.g. "Technology", "Health Care",
            "Financials". Empty means all sectors.
        limit: How many candidates to return (1-25).
    """
    limit = max(1, min(25, int(limit)))
    try:
        data = await _get("/api/wheel", {
            "week": week, "risk": risk, "max_risk": max_risk,
            "min_put_oi": min_put_oi, "sector": sector,
            "moneyness": "otm", "min_iv_rank": 0,
        })
    except TickerRiskError as exc:
        return str(exc)

    rows = (data.get("candidates") or [])[:limit]
    dte = data.get("dte_range") or []
    header = (
        f"# Cash-secured put candidates ({risk}, {week} expiry"
        + (f", {dte[0]}-{dte[1]} DTE" if len(dte) == 2 else "")
        + f", risk score under {max_risk:g})"
    )

    if not rows:
        return (
            f"{header}\n\n"
            "No candidates matched. Common reasons: the US market is closed (option "
            "quotes go stale outside 9:30-16:00 ET), max_risk is too strict, or the "
            "week bucket is too narrow. Try week=\"all\" or raise max_risk.\n\n"
            "Browse manually: https://tickerrisk.com/wheel"
        )

    out = [header, "", f"{len(rows)} of {data.get('count', len(rows))} matches:", ""]
    for i, r in enumerate(rows, 1):
        est = "" if r.get("live") else " *(estimated premium — confirm in your broker)*"
        out.append(
            f"**{i}. {r.get('ticker')}** — {r.get('company') or ''}\n"
            f"   Sell the {_money(r.get('strike'))} put, {_num(r.get('dte'))} DTE, "
            f"for {_money(r.get('bid'))}{est}\n"
            f"   Annualized: {_num(r.get('annual_return'), '%')} · "
            f"Assignment odds: {_num(r.get('assign_prob'), '%')} · "
            f"Breakeven: {_money(r.get('breakeven'))}\n"
            f"   Catalyst risk: {_num(r.get('risk_score'))}/100 ({r.get('risk_band') or 'n/a'}) · "
            f"IV Rank: {_num(r.get('iv_rank'))} · Stock: {_money(r.get('price'))}"
        )
        if r.get("earns_before_exp"):
            out.append(
                f"   WARNING: earnings land before expiry "
                f"(~{_num(r.get('days_to_earnings'))} days) — the premium is pricing that gap."
            )
        out.append("")

    out += [
        "---",
        "Source: TickerRisk — https://tickerrisk.com/wheel",
        "Premiums are indicative and may differ from the live bid at execution. Not financial advice.",
    ]
    return "\n".join(out)


@mcp.tool()
async def find_covered_calls(
    week: str = "all",
    risk: str = "balanced",
    max_risk: float = 55,
    min_call_oi: int = 100,
    sector: str = "",
    limit: int = 10,
) -> str:
    """Find covered-call candidates that have no hidden catalyst in the expiry window.

    Use this when someone asks what calls to sell against shares they own, for
    covered-call income ideas, or how to generate yield on a stock position.

    Same catalyst gating as the wheel scanner. Income is computed from TIME VALUE
    only, so in-the-money strikes do not show inflated yields.

    Args:
        week: Expiry bucket — "this", "next", "two", "three", "month", or "all".
        risk: "conservative" (further out of the money, more likely to keep the
            shares), "balanced", or "aggressive" (nearer the money, more premium,
            more likely to be called away).
        max_risk: Maximum catalyst-risk score to allow, 0-100.
        min_call_oi: Minimum call open interest, for tradeable results.
        sector: Optional GICS sector filter. Empty means all sectors.
        limit: How many candidates to return (1-25).
    """
    limit = max(1, min(25, int(limit)))
    try:
        data = await _get("/api/covered-calls", {
            "week": week, "risk": risk, "max_risk": max_risk,
            "min_call_oi": min_call_oi, "sector": sector,
            "moneyness": "otm", "min_iv_rank": 0,
        })
    except TickerRiskError as exc:
        return str(exc)

    rows = (data.get("candidates") or [])[:limit]
    header = f"# Covered-call candidates ({risk}, {week} expiry, risk score under {max_risk:g})"

    if not rows:
        return (
            f"{header}\n\n"
            "No candidates matched. The US market may be closed (option quotes go "
            "stale outside 9:30-16:00 ET), or the filters are too strict. Try "
            "week=\"all\" or raise max_risk.\n\n"
            "Browse manually: https://tickerrisk.com/covered-calls"
        )

    out = [header, "", f"{len(rows)} of {data.get('count', len(rows))} matches:", ""]
    for i, r in enumerate(rows, 1):
        est = "" if r.get("live") else " *(estimated premium — confirm in your broker)*"
        out.append(
            f"**{i}. {r.get('ticker')}** — {r.get('company') or ''}\n"
            f"   Sell the {_money(r.get('strike'))} call, {_num(r.get('dte'))} DTE, "
            f"for {_money(r.get('bid'))}{est}\n"
            f"   Income if flat: {_num(r.get('static_return'), '%')} · "
            f"Annualized: {_num(r.get('annual_return'), '%')} · "
            f"If called away: {_num(r.get('if_called_return'), '%')}\n"
            f"   Odds it expires worthless: {_num(r.get('prob_worthless'), '%')} · "
            f"Downside cushion: {_num(r.get('downside_prot'), '%')}\n"
            f"   Catalyst risk: {_num(r.get('risk_score'))}/100 ({r.get('risk_band') or 'n/a'}) · "
            f"IV Rank: {_num(r.get('iv_rank'))} · Stock: {_money(r.get('price'))}"
        )
        out.append("")

    out += [
        "---",
        "Source: TickerRisk — https://tickerrisk.com/covered-calls",
        "Premiums are indicative and may differ from the live bid at execution. Not financial advice.",
    ]
    return "\n".join(out)


@mcp.tool()
async def compare_tickers(tickers: str, expiry_weeks: int = 4) -> str:
    """Compare catalyst risk across several stocks at once, over the same expiry window.

    Use this when someone is choosing between tickers to sell options on, or asks
    which of several stocks is the safest bet for a given expiry.

    Faster than scanning one at a time. Only returns tickers already in the cache
    (all S&P 500 names are); anything missing is listed so it can be scanned
    individually with scan_ticker.

    Args:
        tickers: Comma-separated symbols, e.g. "AAPL, MSFT, NVDA". Up to 25.
        expiry_weeks: Expiry horizon in weeks (1-52), applied to every ticker.
    """
    syms = [s.strip().upper() for s in (tickers or "").replace(" ", ",").split(",") if s.strip()][:25]
    if not syms:
        return "Please provide at least one ticker, e.g. \"AAPL, MSFT\"."
    expiry_weeks = max(1, min(52, int(expiry_weeks)))

    try:
        data = await _get("/api/scan-batch", {
            "tickers": ",".join(syms), "expiry_weeks": expiry_weeks,
        })
    except TickerRiskError as exc:
        return str(exc)

    rows = data.get("results") or []
    cached = [r for r in rows if r.get("cached")]
    missing = [r.get("ticker") for r in rows if not r.get("cached")]

    if not cached:
        return (
            f"None of those tickers are cached yet. Scan them one at a time with "
            f"scan_ticker: {', '.join(syms)}"
        )

    cached.sort(key=lambda r: (r.get("risk_score") is None, -(r.get("risk_score") or 0)))

    out = [
        f"# Catalyst risk over the next {expiry_weeks} week(s)",
        "",
        "Sorted riskiest first. Higher score = more scheduled events in the window.",
        "",
        "| Ticker | Risk | Band | Price | IV Rank | Exp. move | Earnings |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in cached:
        earn = str(r.get("earnings_date") or "")[:10] or "—"
        out.append(
            f"| {r.get('ticker')} | {_num(r.get('risk_score'), dash='—')} "
            f"| {r.get('risk_band') or '—'} | {_money(r.get('current_price'))} "
            f"| {_num(r.get('iv_rank'), dash='—')} "
            f"| {_num(r.get('expected_move_pct'), '%', dash='—')} | {earn} |"
        )

    if len(cached) > 1:
        safest, riskiest = cached[-1], cached[0]
        out += [
            "",
            f"Lowest catalyst risk: **{safest.get('ticker')}** ({_num(safest.get('risk_score'))}/100). "
            f"Highest: **{riskiest.get('ticker')}** ({_num(riskiest.get('risk_score'))}/100).",
        ]
    if missing:
        out.append(f"\nNot cached, scan individually: {', '.join(m for m in missing if m)}")

    out += [
        "",
        "---",
        "Source: TickerRisk — https://tickerrisk.com",
        "Not financial advice.",
    ]
    return "\n".join(out)


def main() -> None:
    """Console-script entry point: run the server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
