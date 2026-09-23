"""Parser tests. These run offline against fixtures — no SEC traffic."""
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest
from ingest.edgar import (Filing, Holding, SecClient, _pick_information_table,
                          _value_to_dollars, dedupe_by_period, equity_long_only,
                          parse_information_table, to_weights)

FIX = Path(__file__).parent / "fixtures"
SAMPLE = (FIX / "infotable_sample.xml").read_text()
NONS = (FIX / "infotable_nons.xml").read_text()


# --- parsing ----------------------------------------------------------------
def test_parses_every_line():
    hs = parse_information_table(SAMPLE, date(2024, 3, 31))
    assert len(hs) == 4


def test_parses_without_a_namespace():
    hs = parse_information_table(NONS, date(2024, 3, 31))
    assert len(hs) == 1 and hs[0].cusip == "594918104"
    assert hs[0].shares == 5_000_000        # commas stripped


def test_option_lines_are_marked():
    hs = parse_information_table(SAMPLE, date(2024, 3, 31))
    puts = [h for h in hs if h.put_call]
    assert len(puts) == 1 and puts[0].put_call == "Put"


# --- the units trap ---------------------------------------------------------
def test_pre_2023_values_are_thousands():
    assert _value_to_dollars("1500000", date(2022, 12, 31)) == 1_500_000_000.0


def test_post_2023_values_are_dollars():
    assert _value_to_dollars("1500000", date(2023, 3, 31)) == 1_500_000.0


def test_the_switch_happens_exactly_at_2023():
    assert _value_to_dollars("1000", date(2022, 12, 31)) == 1_000_000.0
    assert _value_to_dollars("1000", date(2023, 1, 1)) == 1_000.0


# --- filtering and weighting ------------------------------------------------
def test_equity_filter_drops_options_and_principal():
    hs = equity_long_only(parse_information_table(SAMPLE, date(2024, 3, 31)))
    assert len(hs) == 2                      # two Apple lines survive
    assert all(h.share_type == "SH" and h.put_call is None for h in hs)


def test_duplicate_cusip_lines_are_aggregated():
    """A manager may file several lines for one issuer by discretion type.
    Treating them as separate positions understates concentration."""
    w = to_weights(equity_long_only(parse_information_table(SAMPLE, date(2024, 3, 31))))
    assert list(w) == ["037833100"]
    assert w["037833100"] == pytest.approx(1.0)


def test_weights_sum_to_one():
    hs = [Holding("A", "a", "COM", 300.0, 1, "SH", None, None),
          Holding("B", "b", "COM", 700.0, 1, "SH", None, None)]
    w = to_weights(hs)
    assert w["A"] == pytest.approx(0.3) and sum(w.values()) == pytest.approx(1.0)


def test_empty_portfolio_yields_no_weights():
    assert to_weights([]) == {}


# --- amendments -------------------------------------------------------------
def _f(period, amend, day):
    return Filing(1, f"acc-{amend}", "13F-HR/A" if amend else "13F-HR",
                  period, day, amend, [])


def test_amendment_supersedes_the_original():
    p = date(2024, 3, 31)
    keep = dedupe_by_period([_f(p, 0, date(2024, 5, 15)), _f(p, 1, date(2024, 6, 1))])
    assert keep[p].amendment_no == 1
    assert keep[p].is_amendment


def test_distinct_periods_are_kept_separately():
    a, b = date(2024, 3, 31), date(2024, 6, 30)
    keep = dedupe_by_period([_f(a, 0, date(2024, 5, 15)), _f(b, 0, date(2024, 8, 14))])
    assert set(keep) == {a, b}


def test_lag_days_is_the_disclosure_delay():
    f = _f(date(2024, 3, 31), 0, date(2024, 5, 15))
    assert f.lag_days == 45


# --- SEC etiquette ----------------------------------------------------------
def test_client_refuses_a_user_agent_without_contact():
    with pytest.raises(ValueError, match="contact"):
        SecClient("some-scraper/1.0")


# --- document discovery -----------------------------------------------------
def _listing(*names):
    return {"directory": {"item": [{"name": n} for n in names]}}


@pytest.mark.parametrize("names,expect", [
    (("primary_doc.xml", "infotable.xml"), "infotable.xml"),
    (("primary_doc.xml", "form13fInfoTable.xml"), "form13fInfoTable.xml"),
    (("primary_doc.xml", "0001-13f_informationtable.xml"), "0001-13f_informationtable.xml"),
    (("primary_doc.xml", "holdings.xml"), "holdings.xml"),
])
def test_finds_the_information_table_under_varied_names(names, expect):
    assert _pick_information_table(_listing(*names)) == expect


def test_returns_none_when_there_is_no_table():
    assert _pick_information_table(_listing("primary_doc.xml")) is None
