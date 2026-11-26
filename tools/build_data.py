# -*- coding: utf-8 -*-
"""重新產生 data/pokemon.json（寶可夢 繁中→日文名）與 data/s2t.json（簡→繁字表）。
平常不需要執行；出了新世代寶可夢時跑一次即可：python tools/build_data.py"""
import csv, io, json, os, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POKE = "https://raw.githubusercontent.com/PokeAPI/pokeapi/master/data/v2/csv/pokemon_species_names.csv"
ST = "https://raw.githubusercontent.com/BYVoid/OpenCC/master/data/dictionary/STCharacters.txt"


def fetch(url):
    return urllib.request.urlopen(url, timeout=30).read().decode("utf-8")


def main():
    names = {}
    for row in csv.DictReader(io.StringIO(fetch(POKE))):
        d = names.setdefault(int(row["pokemon_species_id"]), {})
        d[row["local_language_id"]] = row["name"]
    poke = [[d["4"], d.get("1") or d.get("11")] for _, d in sorted(names.items())
            if d.get("4") and (d.get("1") or d.get("11"))]
    s2t = {}
    for line in fetch(ST).splitlines():
        if line and not line.startswith("#"):
            k, v = line.split("\t")
            v = v.split(" ")[0]
            if k != v:
                s2t[k] = v
    os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)
    for name, obj in (("pokemon.json", poke), ("s2t.json", s2t)):
        with open(os.path.join(ROOT, "data", name), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    print("pokemon:", len(poke), "s2t:", len(s2t))


if __name__ == "__main__":
    main()
