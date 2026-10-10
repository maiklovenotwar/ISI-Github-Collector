# -*- coding: utf-8 -*-
"""Classify a sample of population_sample_1000.csv with LLMs from one or more OpenAI-compatible
providers and compare each model's category against the human label already recorded there.

Requires DATABASE_URL, LOCAL_LLM_URL and FREE_LLMS(_URL) in the environment (source the repo's
.env first, e.g. `set -a; source .env; set +a` on bash). Both provider URLs are base URLs
(no /chat/completions suffix) -- see PROVIDERS below.

Default run (no arguments): stratified sample (10 per category), profile fields only, all
MODEL_RUNS -- the setup of the 2026-08 comparison.

Paired repository-context run: `--sample with-repos --variant both` classifies every
goldstandard org that has repositories in the collector DB twice, once from the profile fields
only and once with its top repositories (name, language, stars, description) appended, and
prints the per-category agreement of both variants side by side.
"""
import argparse
import csv
import io
import json
import os
import random
import sys
import time
import urllib.request
from pathlib import Path

WORKSTREAM_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = WORKSTREAM_ROOT.parent.parent
GOLD_CSV = WORKSTREAM_ROOT / "data" / "goldstandard" / "population_sample_1000.csv"
RESULTS_DIR = WORKSTREAM_ROOT / "data" / "llm_evaluation"

sys.path.insert(0, str(REPO_ROOT / "src"))
from sqlalchemy import create_engine, text  # noqa: E402

# Each provider is an OpenAI-compatible /chat/completions API; "local" needs no auth,
# "fhgenie" is Fraunhofer's internal LLM gateway and needs a bearer token.
PROVIDERS = {
    "local": {
        "base_url": os.environ["LOCAL_LLM_URL"],
        "headers": {},
    },
    "fhgenie": {
        "base_url": os.environ["FREE_LLMS_URL"],
        "headers": {"Authorization": f"Bearer {os.environ['FREE_LLMS']}"},
    },
}

# (provider key, model id) pairs to run. Embedding/image models from the FHGenie catalogue
# (bge-m3, paraphrase-multilingual-mpnet, Z-Image-Turbo) are excluded -- not chat models.
MODEL_RUNS = [
    ("local", "qwen/qwen3.6-35b-a3b"),
    ("local", "qwen/qwen3.6-27b"),
    ("local", "google/gemma-4-31b"),
    ("fhgenie", "nvidia/GLM-5.2-NVFP4"),
    ("fhgenie", "deepseek-ai/DeepSeek-V4-Flash-0731"),
    ("fhgenie", "MiniMaxAI/MiniMax-M2.5"),
    ("fhgenie", "openGPT-X/Teuken-7B-instruct-v0.6"),
]
SAMPLE_SIZE = 150
EMPTY_RETRY_MAX_TOKENS = 4000
CATEGORIES = ["Forschung", "Unternehmen", "Behoerde", "NGO", "Community", "Individual", "Unklar"]

SYSTEM_PROMPT = """Du klassifizierst GitHub-Organisationen anhand ihrer Profildaten in GENAU EINE \
der folgenden Kategorien:

Forschung: Verbindung zu Hochschule oder Forschungseinrichtung erkennbar (auch Uni-Kursprojekte). \
Kommerzielle Bootcamps zaehlen NICHT als Forschung.
Unternehmen: kommerzieller Hintergrund erkennbar (Rechtsform wie GmbH/Inc/Ltd, \
Produktbeschreibung, Firmendomain).
Behoerde: staatliche oder zwischenstaatliche Traegerschaft erkennbar.
NGO: gemeinnuetzige Rechtsform ausdruecklich erkennbar (Foundation, Stiftung, e.V.).
Community: offenes, nicht-kommerzielles Projekt ohne erkennbare Rechtsform (Hobby-Ton, \
kostenlose Hosting-Domain wie github.io/vercel.app).
Individual: der Account gehoert erkennbar einer Einzelperson statt einer Institution \
(Ich-Form, persoenlicher Ton).
Unklar: nicht genug Information fuer eine Einordnung (praktisch alle Felder leer).

Antworte NUR mit exakt einem der sieben Kategorienamen \
(Forschung/Unternehmen/Behoerde/NGO/Community/Individual/Unklar). Keine Erklaerung, kein \
weiterer Text."""


REPOS_PER_ORG = 5
REPO_DESCRIPTION_MAX_CHARS = 150


def build_user_prompt(row, repos=None):
    fields = []
    for label, key in [
        ("Name", "name"),
        ("Firma", "company"),
        ("Bio", "bio"),
        ("Blog/Web", "blog"),
        ("Standort", "location"),
        ("Email", "email"),
    ]:
        value = row[key]
        fields.append(f"{label}: {value if value else '(leer)'}")
    prompt = "Login: " + row["login"] + "\n" + "\n".join(fields)
    if repos:
        lines = []
        for repo in repos:
            description = " ".join((repo["description"] or "").split())
            lines.append(
                f"- {repo['name']} ({repo['language'] or 'keine Sprache'}, "
                f"{repo['stargazers_count'] or 0} Sterne): "
                f"{description[:REPO_DESCRIPTION_MAX_CHARS] if description else '(leer)'}"
            )
        prompt += "\nOeffentliche Repositories der Organisation:\n" + "\n".join(lines)
    return prompt


def call_llm(provider, model, user_prompt, retries=2, max_tokens=1200, timeout=180):
    endpoint = PROVIDERS[provider]["base_url"].rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json", **PROVIDERS[provider]["headers"]}
    # Folded into a single user turn (not a separate system message): some chat templates
    # (e.g. Teuken-7B) reject anything but strict alternating user/assistant roles.
    combined_prompt = SYSTEM_PROMPT + "\n\n" + user_prompt
    payload = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": combined_prompt}],
            "max_tokens": max_tokens,
            "temperature": 0,
        }
    ).encode("utf-8")
    req = urllib.request.Request(endpoint, data=payload, headers=headers, method="POST")
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read())
                return data["choices"][0]["message"]["content"].strip()
        except Exception as exc:  # noqa: BLE001 (best-effort retry loop)
            if attempt == retries - 1:
                return f"ERROR: {exc}"
            time.sleep(2)


def parse_category(raw):
    """Pick the category mentioned LAST in the text: reasoning models often weigh several
    candidates out loud before settling on a final answer at the end."""
    low = raw.lower()
    best_cat, best_pos = None, -1
    for cat in CATEGORIES:
        pos = low.rfind(cat.lower())
        if pos > best_pos:
            best_cat, best_pos = cat, pos
    return best_cat if best_cat else "PARSE_FAIL"


def load_sample():
    with io.open(GOLD_CSV, encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r.get("label") in CATEGORIES]
    random.seed(42)
    return random.sample(rows, min(SAMPLE_SIZE, len(rows)))


def load_stratified_sample(per_category):
    """Draw up to `per_category` rows per label instead of one proportional random sample --
    rare categories (Behoerde/NGO/Individual) would otherwise barely appear at all."""
    with io.open(GOLD_CSV, encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r.get("label") in CATEGORIES]
    random.seed(42)
    by_category = {cat: [r for r in rows if r["label"] == cat] for cat in CATEGORIES}
    sample = []
    for cat_rows in by_category.values():
        sample.extend(random.sample(cat_rows, min(per_category, len(cat_rows))))
    random.shuffle(sample)
    return sample


def fetch_db_rows(logins):
    url = os.environ.get("DATABASE_URL")
    engine = create_engine(url, connect_args={"connection_timeout": 30})
    db_rows = {}
    with engine.connect() as conn:
        for login in logins:
            result = conn.execute(
                text(
                    "SELECT login, name, company, bio, blog, location, email "
                    "FROM organizations WHERE login = :login"
                ),
                {"login": login},
            )
            row = result.fetchone()
            if row:
                db_rows[login] = dict(row._mapping)
    return db_rows


def fetch_repo_context(logins):
    """Top non-fork repositories per org, by stars. Orgs without any are left out."""
    url = os.environ.get("DATABASE_URL")
    engine = create_engine(url, connect_args={"connection_timeout": 30})
    repo_context = {}
    with engine.connect() as conn:
        for login in logins:
            result = conn.execute(
                text(
                    "SELECT r.name, r.description, r.language, r.stargazers_count "
                    "FROM repositories r JOIN organizations o ON o.id = r.organization_id "
                    "WHERE o.login = :login AND (r.fork = 0 OR r.fork IS NULL) "
                    "ORDER BY r.stargazers_count DESC LIMIT :limit"
                ),
                {"login": login, "limit": REPOS_PER_ORG},
            )
            repos = [dict(row._mapping) for row in result]
            if repos:
                repo_context[login] = repos
    return repo_context


def load_gold_rows():
    with io.open(GOLD_CSV, encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if r.get("label") in CATEGORIES]


def run_model(provider, model, sample, db_rows, repo_context=None, suffix=""):
    """Classify `sample` and write one CSV; with `repo_context`, repositories are appended to
    the prompt. Returns {login: parsed category}."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / f"{provider}_{model.replace('/', '_')}{suffix}.csv"
    print(f"=== Provider: {provider} | Model: {model} | {out_path.name} ===", flush=True)
    parsed_by_login = {}
    consecutive_errors = 0
    n_match, n_total = 0, 0
    t0 = time.time()
    with io.open(out_path, "w", newline="", encoding="utf-8") as outf:
        writer = csv.writer(outf)
        writer.writerow(["login", "human_label", "llm_raw", "llm_parsed", "match"])
        for i, row in enumerate(sample):
            db_row = db_rows.get(row["login"])
            if not db_row:
                continue
            repos = repo_context.get(row["login"]) if repo_context else None
            user_prompt = build_user_prompt(db_row, repos)
            raw = call_llm(provider, model, user_prompt)
            if not raw:
                # Empty answer: a reasoning model used up max_tokens before answering.
                raw = call_llm(provider, model, user_prompt, max_tokens=EMPTY_RETRY_MAX_TOKENS)
            # A model that is not loaded/reachable fails every call -- stop instead of
            # writing a full run of errors.
            consecutive_errors = consecutive_errors + 1 if raw.startswith("ERROR:") else 0
            if consecutive_errors >= 5:
                raise SystemExit(f"Aborting {model}: 5 consecutive failed calls, last: {raw}")
            parsed = parse_category(raw)
            parsed_by_login[row["login"]] = parsed
            match = parsed == row["label"]
            n_total += 1
            n_match += int(match)
            writer.writerow([row["login"], row["label"], raw, parsed, match])
            outf.flush()
            if (i + 1) % 20 == 0:
                elapsed = time.time() - t0
                print(
                    f"  {i + 1}/{len(sample)} done, {elapsed:.0f}s elapsed, "
                    f"running match rate {n_match / n_total:.1%}",
                    flush=True,
                )
    print(
        f"  FINAL: {n_match}/{n_total} = {n_match / n_total:.1%} match, "
        f"{time.time() - t0:.0f}s total",
        flush=True,
    )
    return parsed_by_login


def print_paired_summary(sample, baseline, with_repos):
    """Per-category agreement of both variants, plus how the answers moved."""
    labels = {row["login"]: row["label"] for row in sample}
    logins = [login for login in labels if login in baseline and login in with_repos]
    print(f"  --- paired summary (n={len(logins)}) ---", flush=True)
    print(f"  {'category':12} {'n':>4} {'profile':>9} {'+repos':>9}", flush=True)
    for cat in CATEGORIES + [None]:
        subset = [login for login in logins if cat is None or labels[login] == cat]
        if not subset:
            continue
        base_ok = sum(baseline[login] == labels[login] for login in subset)
        repo_ok = sum(with_repos[login] == labels[login] for login in subset)
        print(
            f"  {cat or 'ALL':12} {len(subset):>4} {base_ok / len(subset):>9.1%} "
            f"{repo_ok / len(subset):>9.1%}",
            flush=True,
        )
    fixed = sum(
        baseline[login] != labels[login] and with_repos[login] == labels[login] for login in logins
    )
    broken = sum(
        baseline[login] == labels[login] and with_repos[login] != labels[login] for login in logins
    )
    print(f"  wrong -> right: {fixed}, right -> wrong: {broken}", flush=True)
    gold_unklar = [login for login in logins if labels[login] == "Unklar"]
    moved = [login for login in gold_unklar if with_repos[login] not in ("Unklar", "PARSE_FAIL")]
    print(
        f"  goldstandard 'Unklar' answered with a category once repos are shown: "
        f"{len(moved)}/{len(gold_unklar)}",
        flush=True,
    )


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--sample", choices=["stratified", "with-repos"], default="stratified")
    parser.add_argument("--variant", choices=["profile", "repos", "both"], default="profile")
    parser.add_argument("--models", nargs="*", help="model ids to run (default: all of MODEL_RUNS)")
    parser.add_argument("--limit", type=int, help="only the first N sampled orgs (smoke test)")
    return parser.parse_args()


def main():
    args = parse_args()
    model_runs = [(p, m) for p, m in MODEL_RUNS if not args.models or m in args.models]

    if args.sample == "stratified":
        sample = load_stratified_sample(per_category=10)
    else:
        sample = load_gold_rows()
    need_repos = args.sample == "with-repos" or args.variant != "profile"
    repo_context = fetch_repo_context([row["login"] for row in sample]) if need_repos else {}
    if args.sample == "with-repos":
        sample = [row for row in sample if row["login"] in repo_context]
    if args.limit:
        sample = sample[: args.limit]
    db_rows = fetch_db_rows([row["login"] for row in sample])
    print(
        f"Sample: {args.sample}, n={len(sample)}, "
        f"with repos: {sum(row['login'] in repo_context for row in sample)}",
        flush=True,
    )

    # The stratified profile-only run keeps its original file name (2026-08 comparison).
    tag = "" if args.sample == "stratified" else "__withrepos-sample"
    for provider, model in model_runs:
        baseline = with_repos = None
        if args.variant in ("profile", "both"):
            baseline = run_model(provider, model, sample, db_rows, suffix=tag)
        if args.variant in ("repos", "both"):
            with_repos = run_model(
                provider, model, sample, db_rows, repo_context, suffix=f"{tag}__repos"
            )
        if baseline is not None and with_repos is not None:
            print_paired_summary(sample, baseline, with_repos)


if __name__ == "__main__":
    main()
