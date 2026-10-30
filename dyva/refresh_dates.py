"""Refresh dyva/model-release-dates.json from the Artificial Analysis leaderboard.

model-release-dates.json is curated reference data that ships in the package and is
only READ at runtime (_model_release_date, fuzzy-matched). This pulls a fresh copy:
it scrapes the leaderboard page, walks the embedded Next.js payload for every
{slug, releaseDate} object, and indexes each model's slug/name/shortName to its date.

Keys are normalised with dyva._norm_model — the SAME function the runtime lookup
uses — so the keys we write are exactly the keys dyva will match against.

The page is a Next.js app; its payload shape shifts (the data rides in escaped JSON
inside `self.__next_f.push([1,"…"])` chunks). We decode those chunks with a real JSON
scanner rather than a regex, so a `])` inside the payload can't truncate it. If the
page is restructured so no {slug, releaseDate} objects are found, refresh() fails
loudly rather than writing an empty file.

Stdlib only; run with -I.
"""
import datetime
import json
import urllib.request

from dyva import REL_DATES_FILE, _norm_model

URL = "https://artificialanalysis.ai/leaderboards/models"
SOURCE = "artificialanalysis.ai/leaderboards/models"
_UA = "Mozilla/5.0 (dyva reference-data refresh)"
_MARKER = "self.__next_f.push([1,"


def fetch(url=URL, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:   # noqa: S310 (fixed https URL)
        return r.read().decode("utf-8", "replace")


def _next_chunks(html):
    """Yield each decoded `self.__next_f.push([1,"<chunk>"])` string value. A JSON
    scanner finds each chunk's exact end, so `])` inside the payload can't cut it off."""
    dec = json.JSONDecoder()
    idx = 0
    while True:
        i = html.find(_MARKER, idx)
        if i < 0:
            return
        j = i + len(_MARKER)
        try:
            value, end = dec.raw_decode(html, j)
        except ValueError:
            idx = j + 1
            continue
        idx = end
        if isinstance(value, str):
            yield value


def _walk(o, found):
    if isinstance(o, dict):
        if o.get("releaseDate") and o.get("slug"):
            found[o["slug"]] = o
        for v in o.values():
            _walk(v, found)
    elif isinstance(o, list):
        for v in o:
            _walk(v, found)


def extract(html):
    """Every {slug, releaseDate, name?, shortName?} object in the page's payload."""
    found = {}
    for value in _next_chunks(html):
        if "releaseDate" not in value:
            continue
        payload = value.split(":", 1)[1] if ":" in value[:10] else value
        try:
            doc = json.loads(payload)
        except ValueError:
            continue
        _walk(doc, found)
    return found


def build(found):
    """{_source, _count, _generated, dates} from the extracted model objects. First
    writer wins per key, so a specific name isn't clobbered by a vaguer one."""
    lut = {}
    for m in found.values():
        for key in (m.get("slug"), m.get("name"), m.get("shortName")):
            if not key:
                continue
            k = _norm_model(key)
            if len(k) >= 4:
                lut.setdefault(k, m["releaseDate"])
    return {"_source": SOURCE,
            "_count": len(found),
            "_generated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "dates": lut}


def refresh(html=None, write=True):
    """Pull + rebuild. Returns (status, detail): 'ok' and a count, or 'fail' and why."""
    if html is None:
        html = fetch()
    found = extract(html)
    out = build(found)
    if not out["dates"]:
        return "fail", "no {slug, releaseDate} objects found — page shape changed, update the extraction"
    if write:
        with open(REL_DATES_FILE, "w", encoding="utf-8") as f:
            json.dump(out, f, separators=(",", ":"), sort_keys=True)
            f.write("\n")
    return "ok", "%d models -> %d keys" % (out["_count"], len(out["dates"]))


def main(argv):
    html = None
    if len(argv) > 1 and argv[1] != "-":
        with open(argv[1], encoding="utf-8") as f:
            html = f.read()
    elif len(argv) > 1 and argv[1] == "-":
        import sys
        html = sys.stdin.read()
    status, detail = refresh(html=html)
    import sys
    sys.stderr.write("refresh_dates: %s — %s\n" % (status, detail))
    return 0 if status == "ok" else 1


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv))
