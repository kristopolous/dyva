#!/usr/bin/env python3
"""Attempt-coverage probe: does the race try EVERY candidate host, or does it exhaust the
pool early?

Sends one deliberately-impossible chat request (a huge word list + "what's your favorite word
in this list?") so every host FAILS — the race can never early-exit on a success, so a correct
race should attempt the whole pool. Then it reads the completion record dyva writes to
worker-jobs.jsonl and reports pool size vs. hosts actually tried.

Run ON THE DYVA HOST (so it can read worker-jobs.jsonl), with tools OFF:
    uv run python dyva/probe_attempt_coverage.py \
        --url http://localhost:11434 --model 'gemma4:26b' \
        [--dict /usr/share/dict/american-english-huge] [--words 0] [--jobs ~/.cache/free-ollama/worker-jobs.jsonl]

--words 0 = use the whole dictionary (guarantees context overflow -> failure on every host).
A smaller --words still works if it's enough to overflow the hosts' context.

Reads nothing but the log; sends one request. No DB, no survey state touched.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request


def build_prompt(dict_path, words):
    with open(dict_path, encoding="utf-8", errors="replace") as f:
        vocab = [w.strip() for w in f if w.strip()]
    if words and words > 0:
        vocab = vocab[:words]
    return ("What's your favorite word in this list, and why?\n\n" + " ".join(vocab)), len(vocab)


def send(url, model, content):
    """POST a streaming ollama /api/chat request with NO tools. Drains the stream so the
    race runs to completion; returns (ok, final_reason)."""
    endpoint = url.rstrip("/") + "/api/chat"
    body = json.dumps({"model": model, "stream": True,
                       "messages": [{"role": "user", "content": content}]}).encode()
    req = urllib.request.Request(endpoint, data=body,
                                 headers={"Content-Type": "application/json"})
    last = ""
    try:
        with urllib.request.urlopen(req, timeout=900) as resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if obj.get("error"):
                    last = str(obj.get("error"))
                if obj.get("done"):
                    last = last or obj.get("done_reason", "done")
        return True, last
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        return False, f"HTTP {e.code}: {detail}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def read_last_job(jobs_path, model, since_ts):
    """The newest worker-jobs.jsonl record for this model written after since_ts."""
    try:
        with open(jobs_path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return None
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except Exception:
            continue
        lbl = rec.get("label") or rec.get("model_query") or ""
        et = rec.get("end_time")
        try:
            et = float(et)
        except (TypeError, ValueError):
            et = None
        if model.lower() in str(lbl).lower() and (et is None or et >= since_ts - 2):
            return rec
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:11434", help="dyva base URL")
    ap.add_argument("--model", required=True, help="model query, e.g. 'gemma4:26b'")
    ap.add_argument("--dict", default="/usr/share/dict/american-english-huge")
    ap.add_argument("--words", type=int, default=0, help="0 = whole dictionary")
    ap.add_argument("--jobs", default=os.path.expanduser("~/.cache/free-ollama/worker-jobs.jsonl"))
    ap.add_argument("--dry", action="store_true", help="build the payload and stop (no send)")
    args = ap.parse_args()

    content, nwords = build_prompt(args.dict, args.words)
    print(f"prompt: {nwords} words, {len(content)/1e6:.1f} MB of content, tools OFF")
    if args.dry:
        return

    t0 = time.time()
    ok, reason = send(args.url, args.model, content)
    dt = time.time() - t0
    print(f"request finished in {dt:.0f}s — transport {'ok' if ok else 'ERROR'}: {reason}")

    time.sleep(1.0)   # let the completion record flush
    rec = read_last_job(args.jobs, args.model, t0)
    if not rec:
        print(f"!! no worker-jobs.jsonl record found for '{args.model}' in {args.jobs}")
        print("   (is this the dyva host? is the model query right?)")
        sys.exit(2)

    cand = rec.get("candidates")
    tried = rec.get("tried")
    tally = rec.get("tally") or {}
    print("\n--- race record ---")
    print(f"  pool (candidates): {cand}")
    print(f"  tried:             {tried}")
    print(f"  won:               {rec.get('won')}  outcome: {rec.get('outcome')}")
    print(f"  req_caps:          {rec.get('req_caps')}   tools_offered: {rec.get('tools_offered')}")
    print(f"  num_ctx:           {rec.get('num_ctx')}   prompt_tokens: {rec.get('prompt_tokens')}")
    print(f"  tally (verdicts):  {tally}")

    race = rec.get("race") or []
    if race:
        print(f"  per-host ({len(race)} logged):")
        for r in race:
            print(f"    {r.get('host')}  {r.get('outcome')}  {str(r.get('detail') or '')[:80]}")

    print("\n--- verdict ---")
    if cand and tried is not None:
        pct = 100.0 * tried / cand if cand else 0
        print(f"  attempted {tried}/{cand} = {pct:.0f}% of the pool")
        if tried < cand:
            print("  => EARLY EXHAUSTION: the race stopped before trying every candidate.")
        else:
            print("  => full coverage: every candidate was attempted.")
    won = rec.get("won")
    print(f"  {'100% failure as intended' if not won else 'a host WON (prompt was not impossible enough)'}")


if __name__ == "__main__":
    main()
