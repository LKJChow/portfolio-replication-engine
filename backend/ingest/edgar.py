"""Fetch and parse SEC Form 13F-HR holdings.

WHAT 13F ACTUALLY IS — and what it is not
-----------------------------------------
Institutional managers with over $100M in qualifying US assets file a 13F each
quarter, listing long positions in 13F-eligible securities. It therefore
excludes: short positions, cash, bonds, commodities, foreign-listed equity, and
most derivatives. Replicating a 13F reproduces a manager's *disclosed long US
equity sleeve*, not their portfolio. Anything built on this must say so.

Filings are due 45 days after quarter end, so the earliest an outsider can act
on a holding is up to 45 days (often 135, for a position opened early in the
quarter) after the manager did. `reported_at` and `period_end` are both stored
so the cost of that lag can be measured rather than assumed away.

Amendments (13F-HR/A) restate a period. Later filings for the same period
supersede earlier ones, and `amendment_no` preserves the ordering.

SEC ACCESS RULES
----------------
A descriptive User-Agent containing contact information is mandatory; anonymous
requests are refused. The published ceiling is 10 requests/second and this
client stays well under it.
"""
from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Iterable

import httpx

SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc_nodash}/{doc}"
INDEX = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc_nodash}/index.json"
COMPANY_TICKERS = "https://www.sec.gov/files/company_tickers.json"
BROWSE = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
          "&company={name}&type=13F-HR&dateb=&owner=include&count=40&output=atom")
MIN_INTERVAL = 0.12          # ~8 req/s, under the SEC's stated 10/s ceiling


@dataclass(frozen=True)
class Holding:
    cusip: str
    issuer: str
    title_of_class: str
    value_usd: float          # normalised to dollars; see _value_to_dollars
    shares: float
    share_type: str           # SH (shares) or PRN (principal)
    put_call: str | None      # "Put"/"Call" when the line is an option
    discretion: str | None


@dataclass(frozen=True)
class Filing:
    cik: int
    accession: str
    form: str                 # 13F-HR or 13F-HR/A
    period_end: date
    reported_at: date
    amendment_no: int
    holdings: list[Holding] = field(default_factory=list)

    @property
    def lag_days(self) -> int:
        """Calendar days between the quarter ending and the public disclosure."""
        return (self.reported_at - self.period_end).days

    @property
    def is_amendment(self) -> bool:
        return self.form.upper().endswith("/A")


class SecClient:
    """Rate-limited SEC HTTP client. One instance per ingestion run."""

    def __init__(self, user_agent: str, timeout: float = 30.0):
        if "@" not in user_agent:
            raise ValueError("SEC requires a User-Agent containing contact info")
        self._http = httpx.Client(
            headers={"User-Agent": user_agent,
                     "Accept-Encoding": "gzip, deflate"},
            timeout=timeout, follow_redirects=True)
        self._last = 0.0
        self._subs: dict[int, dict] = {}

    def _throttle(self) -> None:
        wait = MIN_INTERVAL - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()

    def get(self, url: str) -> httpx.Response:
        self._throttle()
        r = self._http.get(url)
        r.raise_for_status()
        return r

    def close(self) -> None:
        self._http.close()

    def __enter__(self): return self
    def __exit__(self, *a): self.close()

    # --- discovery ----------------------------------------------------------
    def resolve_cik(self, name_fragment: str) -> list[tuple[int, str]]:
        """Look a manager up in the ticker index.

        LIMITED ON PURPOSE: company_tickers.json only lists issuers that have a
        stock ticker. Most 13F filers are private partnerships and do not appear
        here at all, and a loose fragment can match an unrelated public company
        with a similar name — "bridgewater" returns Bridgewater Bancshares, a
        Minnesota bank, not Bridgewater Associates. Use resolve_13f_filer for
        managers; this stays for issuer lookups.
        """
        data = self.get(COMPANY_TICKERS).json()
        frag = name_fragment.lower()
        hits = {(int(v["cik_str"]), v["title"])
                for v in data.values() if frag in v["title"].lower()}
        return sorted(hits)

    def resolve_13f_filer(self, name: str) -> list[int]:
        """CIKs of entities matching a name among 13F-HR filers.

        Returns CIKs only. The company-search feed's name field is unreliable —
        on multi-match responses EDGAR emits "ARRAY(0x...)", a leaked Perl
        reference, instead of the company name. Names come from
        entity_name() instead, and every candidate is returned rather than
        guessed between: managers file under near-identical names (feeder
        funds, successor entities), and picking one silently is how the wrong
        portfolio ends up in the database.
        """
        from urllib.parse import quote_plus
        xml = self.get(BROWSE.format(name=quote_plus(name))).text
        root = ET.fromstring(xml)
        out: list[int] = []
        for node in root.iter():
            if _strip_ns(node.tag) == "cik" and node.text:
                cik = int(node.text.strip())
                if cik not in out:
                    out.append(cik)
        return out

    def submissions(self, cik: int) -> dict:
        """Submission history for a CIK, cached for the life of the client."""
        if cik not in self._subs:
            self._subs[cik] = self.get(SUBMISSIONS.format(cik=cik)).json()
        return self._subs[cik]

    def entity_name(self, cik: int) -> str:
        """Authoritative registrant name.

        Taken from the submissions API rather than the company-search feed:
        when that feed returns multiple matches it emits a raw Perl array
        reference ("ARRAY(0x...)") in place of the name and omits
        conformed-name altogether. This endpoint is correct in every case.
        """
        return self.submissions(cik).get("name", "")

    def filings_13f(self, cik: int) -> list[dict]:
        """Every 13F-HR (and amendment) in the submission history."""
        data = self.submissions(cik)
        recent = data.get("filings", {}).get("recent", {})
        out = list(_zip_filings(recent))
        # Older filings spill into separate files once the history is long.
        for extra in data.get("filings", {}).get("files", []):
            page = self.get(f"https://data.sec.gov/submissions/{extra['name']}").json()
            out.extend(_zip_filings(page))
        return [f for f in out if f["form"].upper().startswith("13F-HR")]

    def fetch_filing(self, cik: int, meta: dict) -> Filing:
        acc_nodash = meta["accessionNumber"].replace("-", "")
        listing = self.get(INDEX.format(cik=cik, acc_nodash=acc_nodash)).json()
        doc = _pick_information_table(listing)
        if doc is None:
            raise ValueError(f"no information table in {meta['accessionNumber']}")
        xml = self.get(ARCHIVE.format(cik=cik, acc_nodash=acc_nodash, doc=doc)).text
        return Filing(
            cik=cik,
            accession=meta["accessionNumber"],
            form=meta["form"],
            period_end=_as_date(meta["reportDate"]),
            reported_at=_as_date(meta["filingDate"]),
            amendment_no=int(meta.get("amendmentNo") or 0),
            holdings=parse_information_table(xml),
        )


def _zip_filings(block: dict) -> Iterable[dict]:
    keys = ("accessionNumber", "form", "reportDate", "filingDate", "primaryDocument")
    if not block or "accessionNumber" not in block:
        return []
    return ({k: block[k][i] for k in keys if k in block}
            for i in range(len(block["accessionNumber"])))


def _pick_information_table(listing: dict) -> str | None:
    """The holdings table is the XML that is not the primary cover page.

    Filers name it inconsistently — infotable.xml, form13fInfoTable.xml,
    <something>_informationtable.xml — so match on content of the name rather
    than an exact filename, and fall back to any XML that is not the cover.
    """
    items = listing.get("directory", {}).get("item", [])
    names = [i["name"] for i in items if i["name"].lower().endswith(".xml")]
    for n in names:
        if "infotable" in n.lower().replace("_", "").replace("-", ""):
            return n
    for n in names:
        if "primary_doc" not in n.lower():
            return n
    return None


def _as_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1]


def _text(node, *names: str) -> str | None:
    for child in node.iter():
        if _strip_ns(child.tag) in names and child.text:
            return child.text.strip()
    return None


def _value_to_dollars(raw: str, period_end: date) -> float:
    """13F values were reported in THOUSANDS until 2023, then in dollars.

    The SEC's 2022 amendments moved the value column to whole dollars for
    periods ending on or after 2023-01-01. Mixing the two scales silently
    inflates older portfolios by 1000x, which is the single easiest way to
    produce a completely wrong backtest.
    """
    v = float(raw.replace(",", "").strip())
    return v if period_end >= date(2023, 1, 1) else v * 1000.0


def parse_information_table(xml: str, period_end: date | None = None) -> list[Holding]:
    """Parse a 13F information table into holdings.

    Namespace-agnostic: filers use several namespace URIs and some omit them
    entirely, so tags are matched on local name.
    """
    root = ET.fromstring(xml)
    period = period_end or date(2024, 1, 1)
    out: list[Holding] = []
    for node in root.iter():
        if _strip_ns(node.tag) != "infoTable":
            continue
        cusip = (_text(node, "cusip") or "").strip().upper()
        value = _text(node, "value")
        shares = _text(node, "sshPrnamt")
        if not cusip or value is None or shares is None:
            continue
        out.append(Holding(
            cusip=cusip,
            issuer=(_text(node, "nameOfIssuer") or "").strip(),
            title_of_class=(_text(node, "titleOfClass") or "").strip(),
            value_usd=_value_to_dollars(value, period),
            shares=float(shares.replace(",", "")),
            share_type=(_text(node, "sshPrnamtType") or "SH").strip().upper(),
            put_call=(_text(node, "putCall") or None),
            discretion=(_text(node, "investmentDiscretion") or None),
        ))
    return out


def dedupe_by_period(filings: list[Filing]) -> dict[date, Filing]:
    """Keep the authoritative filing for each period.

    A later amendment restates the period, so the winner is the highest
    amendment number, tie-broken by filing date. Summing a period's original
    and its amendment double-counts the portfolio.
    """
    best: dict[date, Filing] = {}
    for f in sorted(filings, key=lambda x: (x.period_end, x.amendment_no, x.reported_at)):
        best[f.period_end] = f
    return best


def equity_long_only(holdings: Iterable[Holding]) -> list[Holding]:
    """Drop option lines and non-share principal amounts.

    A 13F put line is a long put, not a short stock position, and treating it
    as equity exposure gets the sign of the bet backwards.
    """
    return [h for h in holdings if h.put_call is None and h.share_type == "SH"]


def to_weights(holdings: Iterable[Holding]) -> dict[str, float]:
    """Portfolio weights by CUSIP, normalised to the disclosed sleeve."""
    hs = list(holdings)
    total = sum(h.value_usd for h in hs)
    if total <= 0:
        return {}
    agg: dict[str, float] = {}
    for h in hs:                       # a manager can file several lines per CUSIP
        agg[h.cusip] = agg.get(h.cusip, 0.0) + h.value_usd
    return {k: v / total for k, v in agg.items()}
