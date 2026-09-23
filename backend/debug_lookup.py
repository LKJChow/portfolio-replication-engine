"""Dump what EDGAR actually returns for a company-name search.

    python3 debug_lookup.py baupost

Prints the HTTP status, content type, and the first part of the body, so the
response shape can be inspected rather than inferred.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from find_funds import agent                    # noqa: E402
from ingest.edgar import BROWSE, SecClient      # noqa: E402


def main() -> None:
    term = " ".join(sys.argv[1:]) or "baupost"
    from urllib.parse import quote_plus
    url = BROWSE.format(name=quote_plus(term))
    print(f"GET {url}\n")
    with SecClient(agent()) as c:
        r = c.get(url)
    print(f"status       {r.status_code}")
    print(f"content-type {r.headers.get('content-type')}")
    print(f"length       {len(r.text)}\n")
    print("---- first 1800 chars ----")
    print(r.text[:1800])
    print("---- end ----")


if __name__ == "__main__":
    main()
