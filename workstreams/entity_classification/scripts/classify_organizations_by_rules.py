# -*- coding: utf-8 -*-
"""Apply the ROR/keyword classification rule to the full organizations table (read-only).

Run from anywhere; paths are resolved relative to this script and to the repo root.
Requires the ROR CSV dump (not included in this repo, 35MB) -- download from
https://doi.org/10.5281/zenodo.6347574 and place it at ../ror/v2.10-2026-07-20-ror-data.csv
(or point ROR_CSV below at wherever you put it).
"""
import csv
import io
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

WORKSTREAM_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = WORKSTREAM_ROOT.parent.parent

sys.path.insert(0, str(REPO_ROOT / "src"))
from sqlalchemy import create_engine, text  # noqa: E402

ROR_CSV = WORKSTREAM_ROOT / "ror" / "v2.10-2026-07-20-ror-data.csv"
OUT_SUMMARY = WORKSTREAM_ROOT / "results" / "rule_classification_summary.txt"
OUT_EXAMPLES = WORKSTREAM_ROOT / "results" / "rule_classification_examples.csv"

# ---------- same helpers/rules as classify_ror.py ----------


def normalize_domain(raw):
    if not raw:
        return None
    raw = raw.strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = "http://" + raw
    try:
        host = urlparse(raw).netloc.lower()
    except Exception:
        return None
    host = host.split("@")[-1].split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host or None


def registrable_suffix_variants(host):
    if not host:
        return []
    parts = host.split(".")
    variants = set()
    for i in range(len(parts) - 1):
        variants.add(".".join(parts[i:]))
    return variants


def normalize_name(s):
    if not s:
        return None
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


GOV_DOMAIN_PATTERNS = [
    r"\.gov$",
    r"\.gov\.",
    r"\.mil$",
    r"\.admin\.ch$",
    r"\.gouv\.fr$",
    r"\.gc\.ca$",
    r"\.go\.jp$",
    r"\.europa\.eu$",
    r"\.bund\.de$",
    r"\.gob\.",
    r"\.govt\.",
    r"\.public\.lu$",
]
# Dropped as standalone triggers: "ministry" (religious use), "agency" (marketing
# agencies), "council" (collides as login substring), "amt" (too short, matches
# inside unrelated words) -- all confirmed false-positive sources in full-DB spot check.
GOV_KEYWORDS = [
    "ministry of",
    "ministerio de",
    "ministere de",
    "department of",
    "landesamt",
    "behorde",
    "behörde",
    "bundesministerium",
    "gobierno de",
    "gouvernement du",
    "city of",
    "municipal",
    "county of",
    # 'state of' dropped: also matches generic marketing taglines ("state of your startup")
]
NGO_DOMAIN_PATTERNS = [r"\.ngo$"]
# Dropped bare "ngo" (matched inside "mango/tango/mongo/congo/bingo" etc.) -- use
# word-boundary regex instead of substring for all of these.
NGO_KEYWORDS = [
    "foundation",
    "stiftung",
    "ggmbh",
    "verein",
    "association",
    "trust",
    "charity",
    "nonprofit",
    "non-profit",
    "gemeinnutzig",
    "gemeinnützig",
    "fundacion",
    "fundación",
    "asociacion",
    "asociación",
    "associacao",
    "associação",
    # multilingual test additions (PT/FR/ES abbreviation, word-boundary safe)
    "ong",
    "organizacao nao governamental",
    "organización sin fines de lucro",
]
RESEARCH_KEYWORDS = [
    "university",
    "universit",
    "institute",
    "institut",
    "fraunhofer",
    "max planck",
    "helmholtz",
    "laborator",
    "research group",
    "research center",
    "research centre",
    "forschung",
    "academy of science",
    "national lab",
    "aerospace center",
    "aerospace centre",
    # 'college' dropped bare: collides with place names ("State College, PA")
    # multilingual test additions
    "universidad",
    "universidade",
    "université",
    "universite",
    "università",
    "instituto",
    "istituto",
    "laboratoire",
    "laboratorio",
    "laboratório",
    "大学",
    "研究所",
    "研究院",
]

# 'funder' intentionally excluded: many purely commercial companies (e.g. Adobe)
# appear in ROR only because they fund research grants -- that alone doesn't make
# their GitHub org a research entity (confirmed false positive: typekit/adobe.com).
ROR_TYPE_TO_BUCKET = {
    "education": "Forschung",
    "facility": "Forschung",
    "archive": "Forschung",
    "government": "Behoerde",
    "nonprofit": "NGO",
}


def _word_hits(text_, keywords):
    if not text_:
        return False
    low = text_.lower()
    for kw in keywords:
        pat = r"\b" + re.escape(kw) + r"\b"
        if re.search(pat, low):
            return True
    return False


def gov_hit(domain, text_):
    if domain:
        for pat in GOV_DOMAIN_PATTERNS:
            if re.search(pat, domain):
                return True
    return _word_hits(text_, GOV_KEYWORDS)


def ngo_hit(domain, text_):
    if domain:
        for pat in NGO_DOMAIN_PATTERNS:
            if re.search(pat, domain):
                return True
    if _word_hits(text_, NGO_KEYWORDS):
        return True
    if text_ and re.search(r"\bngo\b", text_.lower()):
        return True
    return False


def research_keyword_hit(text_):
    return _word_hits(text_, RESEARCH_KEYWORDS)


print("Loading ROR CSV...", flush=True)
ror_domain_index = {}
ror_name_index = {}
with io.open(ROR_CSV, encoding="utf-8") as f:
    reader = csv.DictReader(f)
    for row in reader:
        types = [t.strip() for t in row["types"].split(";") if t.strip()]
        display = row.get("names.types.ror_display", "").strip()

        domains_raw = row.get("domains", "").strip()
        website_raw = row.get("links.type.website", "").strip()
        doms = set()
        if domains_raw:
            for d in domains_raw.split(";"):
                nd = normalize_domain(d.strip())
                if nd:
                    doms.add(nd)
        if website_raw:
            nd = normalize_domain(website_raw.split(";")[0])
            if nd:
                doms.add(nd)
        for d in doms:
            ror_domain_index.setdefault(d, []).append((display, types))

        # Acronym field intentionally excluded: short acronyms (RCN, NWS, HST, OGC...)
        # collide with unrelated GitHub orgs of the same name far too often (confirmed
        # false positives). Only match on full multi-word institution names/aliases,
        # which are far less likely to coincide by chance.
        for field in ("names.types.label", "names.types.alias", "names.types.ror_display"):
            raw = row.get(field, "")
            if not raw:
                continue
            for chunk in raw.split(";"):
                chunk = chunk.strip()
                if not chunk:
                    continue
                val = chunk.split(":", 1)[1].strip() if ":" in chunk else chunk
                nn = normalize_name(val)
                if (
                    nn and len(nn.split()) >= 2
                ):  # require multi-word name, no single-token collisions
                    ror_name_index.setdefault(nn, []).append((display, types))
print(f"ROR domain-index: {len(ror_domain_index)}, name-index: {len(ror_name_index)}", flush=True)


def classify(org):
    domain = normalize_domain(org.get("blog"))
    email_domain = None
    if org.get("email") and "@" in org["email"]:
        email_domain = normalize_domain("http://" + org["email"].split("@")[-1])
    all_text = " ".join(
        str(org.get(k) or "") for k in ("name", "bio", "company", "login", "location")
    )

    for dom in (domain, email_domain):
        if not dom:
            continue
        candidates = [dom] + sorted(registrable_suffix_variants(dom), key=len, reverse=True)
        for cand in candidates:
            if cand in ror_domain_index:
                _, types = ror_domain_index[cand][0]
                for t in types:
                    if t in ROR_TYPE_TO_BUCKET:
                        return ROR_TYPE_TO_BUCKET[t], f"ROR-domain:{cand}"

    domain_text = (domain or "") + " " + (email_domain or "")
    if gov_hit(domain, all_text) or any(kw in domain_text for kw in GOV_KEYWORDS):
        return "Behoerde", "gov-heuristic"

    if ngo_hit(domain, all_text):
        return "NGO", "ngo-heuristic"

    for key in (org.get("name"), org.get("login")):
        nn = normalize_name(key)
        if nn and nn in ror_name_index:
            _, types = ror_name_index[nn][0]
            for t in types:
                if t in ROR_TYPE_TO_BUCKET:
                    return ROR_TYPE_TO_BUCKET[t], f"ROR-name:{nn}"

    if research_keyword_hit(all_text):
        return "Forschung", "keyword-heuristic"

    return "Unbestimmt", None


def run_full_db():
    """Stream-classify the full organizations table (read-only) and write results/*.

    Requires DATABASE_URL in the environment (source the repo's .env first, e.g.
    `set -a; source .env; set +a` on bash, before running this script).
    """
    url = os.environ.get("DATABASE_URL")
    engine = create_engine(url, connect_args={"connection_timeout": 30})
    cols = "login, name, company, bio, blog, location, email"

    counts = Counter()
    reason_counts = Counter()
    examples = {}  # label -> list of (login, reason)
    total = 0
    has_blog = 0
    has_bio_or_name = 0

    t0 = time.time()
    with engine.connect().execution_options(stream_results=True) as conn:
        result = conn.execute(text(f"SELECT {cols} FROM organizations"))
        for row in result.yield_per(5000):
            org = dict(zip(["login", "name", "company", "bio", "blog", "location", "email"], row))
            total += 1
            if org.get("blog"):
                has_blog += 1
            if org.get("bio") or org.get("name"):
                has_bio_or_name += 1
            label, reason = classify(org)
            counts[label] += 1
            if reason:
                reason_counts[reason.split(":")[0]] += 1
            if label != "Unbestimmt" and len(examples.setdefault(label, [])) < 15:
                examples[label].append((org["login"], reason))
            if total % 100000 == 0:
                print(f"{total} processed in {time.time()-t0:.0f}s...", flush=True)

    elapsed = time.time() - t0
    print(f"DONE: {total} organizations processed in {elapsed:.0f}s", flush=True)

    OUT_SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    with io.open(OUT_SUMMARY, "w", encoding="utf-8") as f:
        f.write(f"Total organizations: {total}\n")
        f.write(f"Have blog field set: {has_blog} ({has_blog/total:.1%})\n")
        f.write(f"Have bio or name set: {has_bio_or_name} ({has_bio_or_name/total:.1%})\n")
        f.write(f"Processing time: {elapsed:.0f}s\n\n")
        f.write("=== Bucket distribution ===\n")
        for label, c in counts.most_common():
            f.write(f"{label}: {c} ({c/total:.2%})\n")
        f.write("\n=== Signal source breakdown (for classified orgs) ===\n")
        for reason, c in reason_counts.most_common():
            f.write(f"{reason}: {c}\n")

    with io.open(OUT_EXAMPLES, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["label", "login", "reason"])
        for label, items in examples.items():
            for login, reason in items:
                w.writerow([label, login, reason])

    print("Wrote summary + examples.", flush=True)


if __name__ == "__main__":
    run_full_db()
