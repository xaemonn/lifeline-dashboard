"""
Step 5 - Static checks on the generated Power BI Project (no Power BI needed).

  - every DAX reference table[column] / [measure] exists in the model
  - every field used by a report visual exists (column or measure, right table)
  - relationship columns exist and have matching data types
  - every model column is produced by its Power Query partition (CSV header or added column)
  - TMDL indentation uses tabs only

Run: python scripts/05_check_powerbi.py
"""
from pathlib import Path
import json
import re
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SM = ROOT / "powerbi" / "LifelineDashboard.SemanticModel" / "definition"
RP = ROOT / "powerbi" / "LifelineDashboard.Report" / "definition"
errors = []


def unq(n):
    n = n.strip()
    return n[1:-1].replace("''", "'") if n.startswith("'") else n


tables, measures, dtypes, partitions = {}, {}, {}, {}
for f in sorted((SM / "tables").glob("*.tmdl")):
    txt = f.read_text(encoding="utf-8")
    for i, line in enumerate(txt.split("\n"), 1):
        if re.match(r"^ +\S", line):
            errors.append(f"{f.name}:{i} indented with spaces")
    tname = unq(re.search(r"^table (.+)$", txt, re.M).group(1))
    cols = re.findall(r"^\tcolumn (.+)\n\t\tdataType: (\w+)", txt, re.M)
    tables[tname] = {unq(c) for c, _ in cols}
    for c, t in cols:
        dtypes[(tname, unq(c))] = t
    for m in re.finditer(r"^\tmeasure (.+?) =(.*?)(?=^\t\t[a-zA-Z]+:)", txt, re.M | re.S):
        measures[unq(m.group(1))] = (tname, m.group(2))
    sb = re.findall(r"sortByColumn: (.+)", txt)
    for s_ in sb:
        if unq(s_) not in tables[tname]:
            errors.append(f"{tname}: sortByColumn {s_} missing")
    part = re.search(r"source =\n(.*?)\n\n\tannotation", txt, re.S)
    partitions[tname] = part.group(1) if part else ""

# DAX references
for mname, (tname, dax) in measures.items():
    for t, c in re.findall(r"('?[A-Za-z_][\w ]*'?)\[([^\]]+)\]", dax):
        t = unq(t)
        if t in tables and c not in tables[t]:
            errors.append(f"measure [{mname}]: column {t}[{c}] not found")
        elif t not in tables and not t.startswith("@"):
            errors.append(f"measure [{mname}]: table {t} not found")
    for ref in re.findall(r"(?<![\w'\]])\[([^\]]+)\]", dax):
        if ref not in measures and not ref.startswith("@"):
            errors.append(f"measure [{mname}]: measure [{ref}] not found")

# relationships
rel = (SM / "relationships.tmdl").read_text(encoding="utf-8")
for fc, tc in re.findall(r"fromColumn: (.+)\n\ttoColumn: (.+)", rel):
    (ft, fcol), (tt, tcol) = [tuple(unq(x) for x in s.split(".", 1)) for s in (fc, tc)]
    for t, c in ((ft, fcol), (tt, tcol)):
        if c not in tables.get(t, set()):
            errors.append(f"relationship column {t}.{c} missing")
    if dtypes.get((ft, fcol)) != dtypes.get((tt, tcol)):
        errors.append(f"relationship {ft}.{fcol} ({dtypes.get((ft, fcol))}) vs {tt}.{tcol} ({dtypes.get((tt, tcol))})")

# partition outputs vs declared columns
for tname, m in partitions.items():
    csv = re.search(r"powerbi.([\w.]+\.csv)", m)  # first CSV read by the query = the table's main file
    produced = set()
    if csv:
        produced |= set(pd.read_csv(ROOT / "data" / "powerbi" / csv.group(1), nrows=0).columns)
    produced |= set(re.findall(r'Table\.AddColumn\([^,]+, "([^"]+)"', m))
    produced |= set(re.findall(r'Table\.ExpandTableColumn\([^)]*\{"[^"]+"\}, \{"([^"]+)"\}', m))
    produced |= set(re.findall(r'Splitter\.SplitByNothing\(\), \{"(\w+)"\}', m))
    produced |= set(re.findall(r"type table \[(\w+) =", m))
    produced.discard("")
    missing = tables[tname] - produced
    if missing:
        errors.append(f"{tname}: columns not produced by Power Query: {sorted(missing)}")
    for c in re.findall(r'\{"([^"]+)", (?:Int64\.Type|type \w+)\}', m):
        if csv and c not in produced:
            errors.append(f"{tname}: Power Query types unknown column {c}")

# report visuals
n_fields = 0
for vf in RP.rglob("visual.json"):
    s = json.dumps(json.loads(vf.read_text(encoding="utf-8")))
    for kind, ent, prop in re.findall(r'\{"(Measure|Column)": \{"Expression": \{"SourceRef": \{"Entity": "([^"]+)"\}\}, "Property": "([^"]+)"\}', s):
        n_fields += 1
        ok = (prop in measures and measures[prop][0] == ent) if kind == "Measure" else prop in tables.get(ent, set())
        if not ok:
            errors.append(f"{vf.parent.name}: {kind} {ent}.{prop} not in model")

print(f"tables={len(tables)} columns={sum(map(len, tables.values()))} measures={len(measures)} visual field refs={n_fields}")
if errors:
    print(f"{len(errors)} problem(s):")
    for e in errors:
        print("  -", e)
    sys.exit(1)
print("All checks passed.")
