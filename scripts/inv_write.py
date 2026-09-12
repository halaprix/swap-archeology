import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PINS = [23549991, 23550060, 23728292, 24356381, 25896003]


def write(fam, doc):
    doc.setdefault("chain", 1)
    doc.setdefault("schema", 1)
    doc["family"] = fam
    for k in ("deployments", "coverage", "pools", "unresolved"):
        doc.setdefault(k, [])
    p = ROOT / "data/discovery/1" / f"{fam}.json"
    with p.open("w") as output:
        json.dump(doc, output, indent=1)
    print("wrote", p)


def allpins(v=True):
    return {str(p): v for p in PINS}
