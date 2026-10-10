# -*- coding: utf-8 -*-
"""Run the classify_organizations_by_rules rule against rule_validation_sample_100.csv and report
precision/recall per category (Forschung/Behoerde/NGO -- the only ones the rule targets).

Reuses the exact same classify() logic as classify_organizations_by_rules.py (imported, not duplicated) so
the two scripts can never drift apart again. Queries the DB fresh for the 100 goldstandard
logins rather than depending on a stale local dump.

Requires DATABASE_URL in the environment (source the repo's .env first).
"""
import csv
import io
import os
import sys
from pathlib import Path

WORKSTREAM_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = WORKSTREAM_ROOT.parent.parent
GOLD_CSV = WORKSTREAM_ROOT / "data" / "goldstandard" / "rule_validation_sample_100.csv"

sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from classify_organizations_by_rules import classify  # noqa: E402 (also triggers ROR CSV load on import)
from sqlalchemy import create_engine, text  # noqa: E402

# ---------- load goldstandard logins + labels ----------

gold = {}
with io.open(GOLD_CSV, encoding="utf-8") as f:
    for row in csv.DictReader(f):
        gold[row["login"]] = row["manual_label"]
print(f"Loaded {len(gold)} goldstandard rows from {GOLD_CSV.name}")

# ---------- fetch fields for those logins from the DB ----------

url = os.environ.get("DATABASE_URL")
engine = create_engine(url, connect_args={"connection_timeout": 20})
cols = ["login", "name", "company", "bio", "blog", "location", "email"]

orgs = {}
with engine.connect() as conn:
    logins = list(gold.keys())
    placeholders = ", ".join(f":l{i}" for i in range(len(logins)))
    params = {f"l{i}": lg for i, lg in enumerate(logins)}
    rows = conn.execute(
        text(f"SELECT {', '.join(cols)} FROM organizations WHERE login IN ({placeholders})"),
        params,
    )
    for row in rows:
        org = dict(zip(cols, row))
        orgs[org["login"]] = org
print(
    f"Fetched {len(orgs)} of {len(logins)} logins from the DB (some may have been renamed/deleted since)"
)

# ---------- classify and compare ----------

target_buckets = {"Forschung", "Behoerde", "NGO"}
tp = {b: 0 for b in target_buckets}
fn = {b: 0 for b in target_buckets}
false_positives = []
fired_correct = 0
fired_total = 0
results = []

for login, manual_label in gold.items():
    org = orgs.get(login)
    if org is None:
        continue
    pred, reason = classify(org)
    rule_label = None if pred == "Unbestimmt" else pred
    results.append((login, manual_label, rule_label, reason))
    if rule_label is not None:
        fired_total += 1
        if rule_label == manual_label:
            fired_correct += 1
            if manual_label in target_buckets:
                tp[manual_label] += 1
        else:
            false_positives.append((login, manual_label, rule_label, reason))
    if manual_label in target_buckets and rule_label != manual_label:
        fn[manual_label] += 1

print()
print("=== Ergebnis ===")
print(f"Regel hat bei {fired_total}/{len(results)} Orgs etwas erkannt.")
print(f"Davon korrekt (Regel-Label == manuelles Label): {fired_correct}/{fired_total}")
print()
for b in sorted(target_buckets):
    total_manual = sum(1 for _, label, _, _ in results if label == b)
    recall = tp[b] / total_manual if total_manual else 0
    print(
        f"{b}: manuell={total_manual}, korrekt gefunden={tp[b]}, verpasst={fn[b]}, Recall={recall:.0%}"
    )

print()
print("=== False Positives (Regel widerspricht manuellem Label) ===")
for login, manual_label, rule_label, reason in false_positives:
    print(f"{login}: manuell={manual_label}, Regel={rule_label} ({reason})")

print()
print("=== Alle Treffer der Regel (zur Kontrolle) ===")
for login, manual_label, rule_label, reason in results:
    if rule_label is not None:
        mark = "OK" if rule_label == manual_label else "MISMATCH"
        print(f"{mark:8} {login:35} manuell={manual_label:12} regel={rule_label:10} {reason}")
