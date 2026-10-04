"""Soak test: hours of low-rate traffic to GateYourWay, strategies interleaved so they meet the
same good and bad periods. Every ~75 s one logical request with a random strategy; every 10 min a
burst of 3 at once (agent step + title + another chat) with the 'detect' strategy.

python gw_soak.py HOURS
"""
import asyncio
import json
import random
import sys
import time

import httpx

from gw_phase3 import strategy
from gw_probe import body_for

LOG = "gw_soak.jsonl"
STRATS = {"now": {}, "detect": {}, "watchdog": {"watchdog": 20.0}, "hedge": {"hedge": 12.0}}


async def main(hours: float):
    end = time.time() + hours * 3600
    last_burst = 0.0
    async with httpx.AsyncClient() as client:
        while time.time() < end:
            if time.time() - last_burst > 600:
                last_burst = time.time()
                bodies = [body_for("medium", random.randint(0, 10**6)), body_for("small", random.randint(0, 10**6)),
                          body_for("medium", random.randint(0, 10**6))]
                rs = await asyncio.gather(*(strategy(client, b, "detect") for b in bodies))
                for r in rs:
                    with open(LOG, "a", encoding="utf-8") as fh:
                        fh.write(json.dumps({"t": time.time(), "phase": "burst", "strategy": "detect", **r}) + "\n")
                print(time.strftime("%H:%M"), "burst", [(r["ok"], round(r["s"]), r["kinds"]) for r in rs], flush=True)
            name = random.choice(list(STRATS))
            r = await strategy(client, body_for("medium", random.randint(0, 10**6)), name, **STRATS[name])
            with open(LOG, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"t": time.time(), "phase": "single", "strategy": name, **r}) + "\n")
            print(time.strftime("%H:%M"), name, r["ok"], round(r["s"], 1), r["kinds"], flush=True)
            await asyncio.sleep(random.uniform(55, 95))


if __name__ == "__main__":
    asyncio.run(main(float(sys.argv[1])))
