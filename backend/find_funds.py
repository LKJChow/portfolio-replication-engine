"""First-run check: resolve each tracked manager's SEC CIK and filing count.

    cd backend && python3 find_funds.py

Confirms the SEC accepts our User-Agent, that the rate limiter behaves, and that
every manager we intend to track actually files a 13F. Writes funds.json, which
the ingestion step reads — so CIKs are discovered once and recorded, never
hardcoded from memory.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from ingest.edgar import SecClient  # noqa: E402

# Search fragments, not CIKs. A mistyped CIK silently returns another company.
MANAGERS = [
    ("Berkshire Hathaway", "berkshire hathaway inc"),
    ("Pershing Square", "pershing square"),
    ("Tiger Global", "tiger global"),
    ("Baupost Group", "baupost"),
    ("Renaissance Technologies", "renaissance techno"),
    ("Lone Pine Capital", "lone pine"),
    ("Bridgewater Associates", "bridgewater"),
]


def agent() -> str:
    env = Path(__file__).resolve().parents[1] / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("SEC_USER_AGENT"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return "LC Research polochow5@icloud.com"


def main() -> None:
    ua = agent()
    print(f"User-Agent: {ua}\n")
    found, unresolved = {}, []
    with SecClient(ua) as c:
        for label, fragment in MANAGERS:
            try:
                hits = c.resolve_13f_filer(fragment)
            except Exception as exc:                      # noqa: BLE001
                print(f"  {label:<26} ERROR {type(exc).__name__}: {exc}")
                unresolved.append(label)
                continue
            if not hits:
                print(f"  {label:<26} no 13F filer matches that name")
                unresolved.append(label)
                continue

            # Keep only candidates that genuinely have 13F-HR filings. A name
            # match with zero filings is the wrong entity, not a quiet one.
            scored = []
            for cik in hits[:8]:
                try:
                    n = len(c.filings_13f(cik))
                    nm = c.entity_name(cik)
                except Exception:                         # noqa: BLE001
                    continue
                if n:
                    scored.append((n, cik, nm))
            if not scored:
                print(f"  {label:<26} matched {len(hits)} name(s), none with 13F filings")
                unresolved.append(label)
                continue

            scored.sort(reverse=True)
            n, cik, nm = scored[0]
            print(f"  {label:<26} CIK {cik:<10} {nm[:38]:<38} {n} filings")
            found[label] = {"cik": cik, "sec_name": nm, "filings_13f": n}
            for n2, c2, nm2 in scored[1:]:
                print(f"    also: CIK {c2:<10} {nm2[:38]:<38} {n2} filings")

    out = Path(__file__).parent / "funds.json"
    out.write_text(json.dumps(found, indent=2))
    print(f"\nresolved {len(found)}/{len(MANAGERS)} -> {out.name}")
    if unresolved:
        print("unresolved: " + ", ".join(unresolved))


if __name__ == "__main__":
    main()
