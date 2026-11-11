#!/usr/bin/env python3
"""woahllama swarm — a crude multi-agent prototype on top of dyva.

This is NOT part of dyva. dyva stays a failover router; this is separate tooling
that treats dyva's OpenAI/Ollama-compatible endpoint as its model backend. The
interesting angle is dyva's large, varied host pool: a *heterogeneous* swarm can
pin different agents to different model families (dyva routes each to whatever
host has it) and have them collaborate.

Deliberately the dumb version — no A2A, no protocol, no framework. Just asyncio,
a shared blackboard, and HTTP. The point is to find out whether a swarm over the
pool is useful; standardize (e.g. A2A) only once there's something to
interoperate with.

Usage:
    ./swarm.py "research the tradeoffs of X and recommend one"
    ./swarm.py "..." -n 5
    ./swarm.py "..." -a "researcher:qwen3,skeptic:llama3.2,writer:*" -r 3

Point it at a non-default dyva with:  DYVA_URL=http://host:port ./swarm.py ...
"""
import argparse
import asyncio
import os
import time

import aiohttp

DYVA_URL = os.environ.get("DYVA_URL", "http://localhost:11434").rstrip("/")


async def ask(session, model, messages):
    """One non-streaming completion through dyva. Returns the reply text.

    dyva does the failover/racing behind /api/chat, so from here a model name
    (or '*' for any) is all we supply — whichever host answers, answers."""
    async with session.post(f"{DYVA_URL}/api/chat",
                            json={"model": model, "messages": messages,
                                  "stream": False}) as r:
        r.raise_for_status()
        data = await r.json()
    return ((data.get("message") or {}).get("content") or "").strip()


class Blackboard:
    """The shared space agents talk through — the dumbest thing that works: a
    lock-guarded list of (who, text) any agent can append to and read back."""

    def __init__(self):
        self.notes = []
        self._lock = asyncio.Lock()

    async def post(self, who, text):
        async with self._lock:
            self.notes.append((who, text))

    def read(self):
        return "\n\n".join(f"[{who}]\n{text}" for who, text in self.notes)


def emit(tag, text):
    """Stream a contribution up to the caller as soon as it lands."""
    print(f"\n=== {tag} ===\n{text}", flush=True)


async def worker(session, board, name, model, task, shared_context):
    system = (f"You are {name}, one agent in a swarm working a shared task. Do "
              "your part concisely and in your own angle. If notes from other "
              "agents are given, build on them instead of repeating. Return only "
              "your contribution.")
    msgs = [{"role": "system", "content": system}]
    if shared_context:
        msgs.append({"role": "user",
                     "content": "Notes from the other agents so far:\n\n" + shared_context})
    msgs.append({"role": "user", "content": task})
    t0 = time.time()
    try:
        out = await ask(session, model, msgs)
    except Exception as e:                       # a dead host/model shouldn't sink the swarm
        out = f"(failed: {e})"
    emit(f"{name} · {model} · {time.time() - t0:.1f}s", out)
    await board.post(name, out)
    return name, out


async def run_swarm(task, agents, rounds):
    board = Blackboard()
    timeout = aiohttp.ClientTimeout(total=600)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for rnd in range(rounds):
            # round 1 works cold; later rounds see the whole blackboard, which
            # is where "agents talking to each other" actually happens.
            snapshot = board.read() if rnd > 0 else ""
            emit(f"ROUND {rnd + 1}/{rounds}", f"{len(agents)} agents working…")
            await asyncio.gather(*[
                worker(session, board, a["name"], a["model"], task, snapshot)
                for a in agents
            ])
        # one coordinator pass folds the blackboard into a single answer for the
        # caller — the "report up" step.
        emit("SYNTHESIS", "combining agent output…")
        final = await ask(session, agents[0]["model"], [
            {"role": "system", "content":
                "You are the swarm coordinator. Synthesize the agents' notes into "
                "one clear, complete answer for the user. Resolve disagreements; "
                "do not just concatenate."},
            {"role": "user", "content":
                f"Task:\n{task}\n\nAgent notes:\n\n{board.read()}"},
        ])
        emit("FINAL", final)
        return final


def parse_agents(spec, n, model):
    """--agents 'researcher:qwen3,skeptic:llama3.2,writer:*' pins models per
    agent (the heterogeneous case). Otherwise n copies on one model."""
    if spec:
        agents = []
        for i, part in enumerate(spec.split(",")):
            nm, _, md = part.partition(":")
            agents.append({"name": nm.strip() or f"agent{i + 1}",
                           "model": (md.strip() or model)})
        return agents
    return [{"name": f"agent{i + 1}", "model": model} for i in range(n)]


def main():
    ap = argparse.ArgumentParser(description="crude dyva-backed agent swarm")
    ap.add_argument("task", help="the task for the swarm")
    ap.add_argument("-n", type=int, default=3, help="number of agents (default 3)")
    ap.add_argument("-m", "--model", default="*",
                    help="default model; dyva routes it ('*' = any host, the default)")
    ap.add_argument("-a", "--agents",
                    help="comma list 'name:model,name:model' — pin agents to different models")
    ap.add_argument("-r", "--rounds", type=int, default=2,
                    help="collaboration rounds (default 2; round 2+ sees the blackboard)")
    args = ap.parse_args()
    agents = parse_agents(args.agents, args.n, args.model)
    print(f"swarm: {len(agents)} agents via {DYVA_URL} — "
          + ", ".join(f"{a['name']}({a['model']})" for a in agents), flush=True)
    asyncio.run(run_swarm(args.task, agents, args.rounds))


if __name__ == "__main__":
    main()
