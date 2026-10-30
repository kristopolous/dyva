"""Refresh dyva/model-prices.json from the OpenRouter catalog. Out-of-band.

model-prices.json is curated reference data that ships in the package and is only
READ at runtime (load_model_prices / _price_of) — dyva never rewrites it. This is
the maintainer command that pulls a fresh copy, so a price change lands in a git
diff you can eyeball and commit, not silently at request time:

    python -m dyva.refresh_prices              # fetch OpenRouter, rewrite the file
    python -m dyva.refresh_prices catalog.json # build from a saved catalog (offline)
    python -m dyva.refresh_prices -            # ... read the catalog JSON from stdin

OpenRouter quotes USD per TOKEN; dyva stores USD per MILLION tokens. For each
family ALREADY in the file we take the median of the matching models' rates and
rewrite that family's numbers; a family OpenRouter barely carries keeps its curated
value (a thin sample shouldn't clobber a hand-set number). The family set, _comment,
_match_note and _default are left alone — adding or removing a family is a curation
call, not a refresh. Models are bucketed by dyva.price_family_key, the SAME rule the
runtime lookup uses, so the buckets match how prices will later be looked up.

Stdlib only; run it with -I (it reads a downloaded catalog as data).
"""
import datetime
import json
import statistics
import sys
import urllib.request

from dyva import MODEL_PRICES_FILE, price_family_key

CATALOG_URL = "https://openrouter.ai/api/v1/models"
SOURCE = "openrouter.ai/api/v1/models"
MIN_SAMPLES = 2           # fewer matches than this -> keep the curated value
_UA = "dyva-refresh-prices/1.0 (+reference-data refresh)"


def per_mtok(x):
    """OpenRouter price string (USD per token) -> float USD per million tokens."""
    return float(x) * 1_000_000.0


def fetch(url=CATALOG_URL, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:   # noqa: S310 (fixed https URL)
        return json.loads(r.read().decode("utf-8"))


def build(models, existing):
    """(refreshed-dict, changed) from an OpenRouter model list + the existing file.

    `changed` maps each family whose numbers actually moved to (old, new), for a
    human-readable summary. Preserves the existing family order and all meta keys;
    stamps _source and _generated."""
    families = list(existing.get("families", {}))
    buckets = {f: {"prompt": [], "completion": []} for f in families}

    for m in models or []:
        mid = (m or {}).get("id") or ""
        pricing = (m or {}).get("pricing") or {}
        try:
            prompt, completion = per_mtok(pricing["prompt"]), per_mtok(pricing["completion"])
        except (KeyError, TypeError, ValueError):
            continue
        if prompt <= 0:                      # a free/zero endpoint (e.g. ':free') is not a price signal
            continue
        fam = price_family_key(mid, families)
        if fam is None:
            continue
        buckets[fam]["prompt"].append(prompt)
        buckets[fam]["completion"].append(completion)

    out_families, changed = {}, {}
    for f in families:
        ps, cs = buckets[f]["prompt"], buckets[f]["completion"]
        if len(ps) >= MIN_SAMPLES:
            new = {"prompt": round(statistics.median(ps), 3),
                   "completion": round(statistics.median(cs), 3)}
            out_families[f] = new
            if new != existing["families"][f]:
                changed[f] = (existing["families"][f], new)
        else:
            out_families[f] = existing["families"][f]   # keep the curated value

    result = dict(existing)            # preserve _comment / _match_note / _default + order
    result["families"] = out_families
    result["_source"] = SOURCE
    result["_generated"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return result, changed


def refresh(catalog=None, write=True):
    """Pull + rebuild. Returns (status, detail): 'ok' and how many families moved,
    or 'fail' and why. `catalog` is an already-fetched OpenRouter doc/list (tests
    and the driver pass one); None fetches live."""
    doc = catalog if catalog is not None else fetch()
    models = doc.get("data") if isinstance(doc, dict) else doc
    if not isinstance(models, list):
        return "fail", "catalog had no 'data' list — shape changed?"
    with open(MODEL_PRICES_FILE, encoding="utf-8") as f:
        existing = json.load(f)
    result, changed = build(models, existing)
    if write:
        with open(MODEL_PRICES_FILE, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
            f.write("\n")
    return "ok", "%d models, %d/%d families updated" % (
        len(models), len(changed), len(result.get("families", {})))


def _load_catalog(argv):
    if len(argv) > 1 and argv[1] == "-":
        return json.load(sys.stdin)
    if len(argv) > 1:
        with open(argv[1], encoding="utf-8") as f:
            return json.load(f)
    return fetch()


def main(argv):
    doc = _load_catalog(argv)
    models = doc.get("data") if isinstance(doc, dict) else doc
    if not isinstance(models, list):
        sys.stderr.write("refresh_prices: catalog had no 'data' list — shape changed?\n")
        return 1

    with open(MODEL_PRICES_FILE, encoding="utf-8") as f:
        existing = json.load(f)
    result, changed = build(models, existing)

    with open(MODEL_PRICES_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
        f.write("\n")

    sys.stderr.write("refresh_prices: %d models, %d/%d families updated\n"
                     % (len(models), len(changed), len(result.get("families", {}))))
    for fam, (old, new) in sorted(changed.items()):
        sys.stderr.write("  %-16s %s -> %s\n" % (fam, old, new))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
