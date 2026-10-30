"""Incrementally refresh dyva/ollama-latest-sizes.json from ollama.com/library.

dyva reads ONE thing from this file — params_b per tag (_load_ollama_params), used to
group the model list by size — and params_b is encoded in the tag name (`3b` -> 3.0),
so a model's tags page alone has everything we need (we also keep digest/bytes/size_str).

The pull is incremental and cheap: the library index sorted newest-first lists models
with the most recently updated at the top, so we walk it top-down and STOP at the first
model already in the file — everything below it we have already captured. A refresh
therefore fetches only the genuinely-new models (usually a handful), one tags page each.
No wget mirror, no re-crawl.

The tag-page scrape regexes are kept in sync with graflex/build_ollama_sizes.py (which
builds the same file from a full mirror); only the parameter count differs — here it
comes from the tag name, there from each tag's detail page.

Stdlib only; run with -I.
"""
import datetime
import json
import re
import time
import urllib.request

from dyva import OLLAMA_SIZES_FILE

INDEX_URL = "https://ollama.com/library?sort=newest"
TAGS_URL = "https://ollama.com/library/%s/tags"
SOURCE = "ollama.com/library (incremental, newest-first)"
_UA = "dyva reference-data refresh (incremental ollama library sizes)"
_DELAY = 0.5                 # be polite between live page fetches
MAX_NEW = 500                # safety bound on one incremental run

_LIB_LINK = re.compile(r'href="/library/([^":/?]+)"')          # index page: a model link
_ANCHOR = re.compile(r'href="/library/([^":/]+):([^"/]+)"')    # tags page: model:tag rows
_SIZE = re.compile(r'([0-9]+(?:\.[0-9]+)?)\s*([GMK])B', re.I)
_DIGEST = re.compile(r'\b([0-9a-f]{12,64})\b')
_BLUE_LATEST = re.compile(r'border-blue-500[^>]*>\s*latest\s*</span>', re.I)
_PNUM = re.compile(r'([0-9]+(?:\.[0-9]+)?)\s*([BMK])', re.I)
_MULT = {"K": 1e3, "M": 1e6, "G": 1e9}


def _bytes(num, unit):
    return int(float(num) * _MULT[unit.upper()])


def _params_b(name):
    """Billions of parameters from a tag name: '3b'->3.0, '270m'->0.27, else None."""
    m = _PNUM.search(name or "")
    if not m:
        return None
    n, u = float(m.group(1)), m.group(2).upper()
    return round(n * {"B": 1.0, "M": 1e-3, "K": 1e-6}[u], 4)


def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:   # noqa: S310 (fixed https URL)
        return r.read().decode("utf-8", "replace")


def index_models(html):
    """Model names from the newest-first library index, in page order, de-duplicated."""
    out, seen = [], set()
    for m in _LIB_LINK.finditer(html):
        name = m.group(1)
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


def parse_tags(html, model):
    """{tag: {size_str, bytes, digest, params_b?}} for one model's tags page, with
    'latest' resolved to whichever concrete tag carries the blue 'latest' alias pill."""
    anchors = [m for m in _ANCHOR.finditer(html) if m.group(1) == model]
    seen, ordered = set(), []
    for m in anchors:                       # the page renders two anchors/row; keep the first per tag, in order
        if m.group(2) not in seen:
            seen.add(m.group(2))
            ordered.append(m)
    out = {}
    for i, m in enumerate(ordered):
        tag = m.group(2)
        seg = html[m.start(): ordered[i + 1].start() if i + 1 < len(ordered) else len(html)]
        sm = _SIZE.search(seg)
        if not sm:
            continue
        dm = _DIGEST.search(seg)
        info = {"size_str": "%s%sB" % (sm.group(1), sm.group(2).upper()),
                "bytes": _bytes(sm.group(1), sm.group(2)),
                "digest": dm.group(1) if dm else None}
        pb = _params_b(tag)
        if pb is not None:
            info["params_b"] = pb
        out[tag] = info
    if "latest" not in out:                 # 'latest' is a badge on a concrete tag, not always its own row
        bm = _BLUE_LATEST.search(html)
        if bm:
            prev = [a for a in ordered if a.start() < bm.start()]
            if prev and prev[-1].group(2) in out:
                out["latest"] = dict(out[prev[-1].group(2)])
    # An explicit ':latest' row names no size, so it has no params_b — but it's the
    # SAME blob as a sized tag (shared digest). Adopt that tag's count, so dyva's
    # bare-name size grouping (flat[model]) resolves, matching the mirror build.
    lat = out.get("latest")
    if lat and lat.get("params_b") is None and lat.get("digest"):
        for tag, info in out.items():
            if tag != "latest" and info.get("digest") == lat["digest"] and info.get("params_b") is not None:
                lat["params_b"] = info["params_b"]
                break
    return out


def _entry(tags):
    e = {"tags": tags}
    if "latest" in tags:
        e["latest"] = tags["latest"]
    return e


def refresh(index_html=None, tags_fetcher=None, write=True):
    """Pull only the models newer than what's on disk. Returns (status, detail).
    `index_html`/`tags_fetcher` are injection points for tests (no network)."""
    with open(OLLAMA_SIZES_FILE, encoding="utf-8") as f:
        existing = json.load(f)
    models = dict(existing.get("models") or {})
    known = set(models)

    live = tags_fetcher is None
    if index_html is None:
        index_html = fetch(INDEX_URL)
    if tags_fetcher is None:
        tags_fetcher = lambda name: fetch(TAGS_URL % name)   # noqa: E731

    new = {}
    for name in index_models(index_html):
        if name in known:
            break                           # seen it -> everything below is already captured -> done
        if len(new) >= MAX_NEW:
            break
        try:
            if live and new:
                time.sleep(_DELAY)
            tags = parse_tags(tags_fetcher(name), name)
        except Exception:
            continue
        if tags:
            new[name] = _entry(tags)

    if not new:
        return "ok", "no new models (up to date)"

    models.update(new)
    out = dict(existing)
    out["models"] = models
    out["_count"] = len(models)
    out["_source"] = SOURCE
    out["_generated"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if write:
        with open(OLLAMA_SIZES_FILE, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, sort_keys=True)
            f.write("\n")
    sample = ", ".join(list(new)[:8]) + ("…" if len(new) > 8 else "")
    return "ok", "%d new model(s): %s" % (len(new), sample)


def main(argv):
    import sys
    status, detail = refresh()
    sys.stderr.write("refresh_sizes: %s — %s\n" % (status, detail))
    return 0 if status == "ok" else 1


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv))
