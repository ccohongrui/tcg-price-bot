# -*- coding: utf-8 -*-
"""把 names.py 與 data/*.json 打包成 worker/src/data.json（Cloudflare Worker 用）。
改了 names.py 之後執行：python tools/build_worker_data.py，再重新部署 Worker。"""
import json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import names as N  # noqa: E402


def load(name):
    with open(os.path.join(ROOT, "data", name), encoding="utf-8") as f:
        return json.load(f)


def expand(table):
    return [[zh.strip(), ja] for zhs, ja in table.items() for zh in zhs.split("|")]


out = {
    "s2t": load("s2t.json"),
    "entries": {
        "pokemon": load("pokemon.json") + expand(N.POKEMON_EXTRA),
        "onepiece": expand(N.ONEPIECE),
        "yugioh": expand(N.YUGIOH),
    },
    "modifiers": N.MODIFIERS,
    "display": N.DISPLAY,
}
dst = os.path.join(ROOT, "worker", "src", "data.json")
with open(dst, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
print("wrote", dst, os.path.getsize(dst), "bytes")
