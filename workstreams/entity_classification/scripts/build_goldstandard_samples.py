"""Merge the raw classification batches into two canonical goldstandard files.

Run once from the workstream root: python scripts/build_goldstandard_samples.py

Produces:
  data/goldstandard/population_sample_1000.csv
      Pure-random sample (n=1000) used for population-percentage estimates.
      Composition: pilot batch 1 segment C (n=50, itbh-at..webreload) + batch 2
      (n=500) + batch 3 (n=200) + batch 4 (n=250).

  data/goldstandard/rule_validation_sample_100.csv
      The original pilot's 100 rows (segments A+B+C, confidence-rated), used to
      measure the ROR/keyword rule's precision/recall on Forschung/Behoerde/NGO.
      Segments A (n=20, high-follower) and B (n=30, mid-tier-with-blog) are
      intentionally NOT part of the population sample -- they were drawn to
      stress-test the rule on well-documented orgs, not to be representative.
"""

import csv
import io
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent / "data" / "goldstandard"

# --- rule_validation_sample_100.csv: batch 1 as-is (segments A+B+C) ---
with io.open(BASE / "raw_batch1_pilot_100.csv", encoding="utf-8") as f:
    rows = list(csv.DictReader(f))
assert len(rows) == 100, f"expected 100 rows in batch 1, got {len(rows)}"

segment_labels = ["A"] * 20 + ["B"] * 30 + ["C"] * 50
for row, seg in zip(rows, segment_labels):
    row["segment"] = seg

with io.open(BASE / "rule_validation_sample_100.csv", "w", encoding="utf-8", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["login", "manual_label", "confidence", "segment"])
    w.writeheader()
    w.writerows(rows)
print(f"wrote rule_validation_sample_100.csv ({len(rows)} rows)")

# --- population_sample_1000.csv: batch1-segmentC + batch2 + batch3 + batch4 ---
segment_c = [r for r in rows if r["segment"] == "C"]


def load_simple(path, batch_name):
    with io.open(path, encoding="utf-8") as f:
        out = []
        for r in csv.DictReader(f):
            out.append({"login": r["login"], "label": r["label"], "batch": batch_name})
        return out


pop_rows = []
for r in segment_c:
    pop_rows.append({"login": r["login"], "label": r["manual_label"], "batch": "batch1_segmentC"})
pop_rows += load_simple(BASE / "raw_batch2_random_500.csv", "batch2_random_500")
pop_rows += load_simple(BASE / "raw_batch3_random_200.csv", "batch3_random_200")
pop_rows += load_simple(BASE / "raw_batch4_random_250.csv", "batch4_random_250")

assert len(pop_rows) == 1000, f"expected 1000 population rows, got {len(pop_rows)}"
logins = [r["login"] for r in pop_rows]
assert len(logins) == len(set(logins)), "duplicate logins found in population sample"

with io.open(BASE / "population_sample_1000.csv", "w", encoding="utf-8", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["login", "label", "batch"])
    w.writeheader()
    w.writerows(pop_rows)
print(f"wrote population_sample_1000.csv ({len(pop_rows)} rows, all unique)")

# --- sanity: print final distribution ---
from collections import Counter

c = Counter(r["label"] for r in pop_rows)
print("\nPopulation sample distribution (n=1000):")
for label, cnt in c.most_common():
    print(f"  {label}: {cnt} ({cnt/1000:.1%})")
