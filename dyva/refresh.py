"""Refresh ALL of dyva's externally-sourced reference JSONs in one go.

dyva ships several curated reference files that are only READ at runtime; the ones
whose values are EXTERNAL FACTS (prices, release dates, …) can be pulled fresh from
an upstream. This is the single out-of-band command that does that — `dyva
--refresh-reference` — so a maintainer refreshes everything at once and commits the
diff, instead of a drawer of one-off scripts. (The other reference files —
classifiers, regex pattern sets, fingerprints — are authored judgment, not facts to
pull, so they're not here.)

Each source is independent and fault-isolated: one upstream being down or
restructured is reported and the rest still run. Exit is non-zero only if a source
actually FAILED (a source that can't be auto-pulled is 'skip', not a failure).

    dyva --refresh-reference                 # refresh every pullable source
    dyva --refresh-reference prices dates    # just these
    python -m dyva.refresh                   # same, without the console script

Stdlib only; run with -I.
"""
import sys

from dyva import refresh_dates, refresh_prices, refresh_sizes


def _prices():
    return refresh_prices.refresh()


def _dates():
    return refresh_dates.refresh()


def _sizes():
    return refresh_sizes.refresh()


# name -> (what it writes, puller). Order is the run order.
SOURCES = [
    ("prices", "model-prices.json", _prices),
    ("dates", "model-release-dates.json", _dates),
    ("sizes", "ollama-latest-sizes.json", _sizes),
]


def run(only=None, out=sys.stderr):
    """Run the selected sources (all if `only` is empty). Returns a process exit
    code: 0 unless a source FAILED. Each line of the report is `name: status — detail`."""
    names = [n for n, _, _ in SOURCES]
    if only:
        unknown = [n for n in only if n not in names]
        if unknown:
            out.write("refresh: unknown source(s): %s (known: %s)\n"
                      % (", ".join(unknown), ", ".join(names)))
            return 2
    selected = [s for s in SOURCES if (not only or s[0] in only)]

    failed = False
    out.write("refreshing reference data: %s\n" % ", ".join(n for n, _, _ in selected))
    for name, _file, puller in selected:
        try:
            status, detail = puller()
        except Exception as e:                       # one source down ≠ abort the rest
            status, detail = "fail", "%s: %s" % (type(e).__name__, e)
        if status == "fail":
            failed = True
        out.write("  %-7s %-5s %s\n" % (name, status, detail))
    return 1 if failed else 0


def main(argv):
    return run(only=argv[1:] or None)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
