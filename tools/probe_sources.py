"""Live provider probe. Run on the machine you actually use (home IP), not in a datacenter.

    python tools/probe_sources.py --acknowledge-terms [--sources NBA_STATS NBA_LIVE] [--out probe_report.json] [--save-samples DIR]

Enabling a source records that YOU accepted responsibility for its terms (see docs/LICENSING_AND_COMMERCIAL_CONCERNS.md); the flag
--acknowledge-terms is your statement of that. Keyed sources are only probed when their environment variable is set
(BALLDONTLIE_API_KEY, SPORTSDATAIO_API_KEY). Makes about ten small requests per source, spaced out; no proxies or evasion.
"""
import argparse, json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from nbalab.sources import SourceRouter
from nbalab.store import Store


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--acknowledge-terms", action="store_true", help="state that you have read each enabled source's terms")
    ap.add_argument("--sources", nargs="*", default=["NBA_STATS", "NBA_LIVE"])
    ap.add_argument("--out", default="probe_report.json")
    ap.add_argument("--save-samples", default=None)
    a = ap.parse_args()
    if not a.acknowledge_terms:
        print("Refusing to make requests without --acknowledge-terms (see the docstring). Nothing was requested.")
        return 2
    store = Store(":memory:")
    r = SourceRouter(store)
    names = list(a.sources)
    for n in ("BALLDONTLIE", "SPORTSDATAIO"):
        if n in names and not os.environ.get(r.sources[n].key_env):
            print(f"skipping {n}: {r.sources[n].key_env} not set")
            names.remove(n)
    for n in names:
        s = r.sources[n]
        r.enable(n, f"I have read {s.terms_url} and accept responsibility for how I use this source")
    res = r.probe(names, save_dir=a.save_samples)
    w = max(len(x["capability"]) for x in res)
    for x in res:
        print(f"{x['source']:<13} {x['capability']:<{w}}  {x['outcome']:<11} rows={x['rows']:<6} {x['elapsed_ms']:>6.0f}ms  {x['detail'][:90]}")
    json.dump({"results": res, "matrix": r.matrix()}, open(a.out, "w"), indent=1, default=str)
    ok = sum(1 for x in res if x["outcome"] == "ok")
    print(f"\n{ok}/{len(res)} probes returned data. Report written to {a.out}")
    if os.environ.get("GITHUB_ACTIONS"):
        body = "%0A".join(f"{x['source']} {x['capability']}: {x['outcome']} rows={x['rows']} {x['detail'][:80]}".replace("%", "%25") for x in res)
        print(f"::notice title=provider probe ({ok}/{len(res)} ok)::{body}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
