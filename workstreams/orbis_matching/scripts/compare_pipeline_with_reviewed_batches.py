#!/usr/bin/env python3
"""Vergleicht die Pipeline-Ausgabe gegen die in batch01-04_review.md dokumentierten
Soll-Ergebnisse. Einmaliges Verifikations-Skript (Teil C des Plans), kein Teil der
Produktions-Pipeline - daher hier im workstreams-Ordner statt in scripts/.
"""

import csv
import sys
from pathlib import Path
from typing import List, Tuple

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "scripts" / "enrichment"))

from orbis_matching_pipeline import (  # noqa: E402
    apply_hq_resolution_to_match_result,
    apply_hq_signals,
    build_match_candidates,
    extract_domain,
    fetch_organizations,
)

from github_collector.enrichment.hq_detection import resolve_headquarters  # noqa: E402
from github_collector.enrichment.orbis_client import (  # noqa: E402
    OrbisClient,
    extract_root_domain,
    is_domain_blacklisted,
    load_blog_blacklist,
)
from github_collector.enrichment.orbis_matching_logic import (  # noqa: E402
    MatchStatus,
    ScoringConfig,
    analyze_matches,
    load_matching_config,
)

# Soll-Zuordnungen aus den von Hand geprüften Chargen: eine Zeile je Organisation mit den
# Spalten login, expected (erwartete BVDID oder Sonder-Marker) und note. Die Datei enthält
# aus Orbis abgeleitete Kennungen und liegt deshalb nur lokal (data/ ist von Git ausgeschlossen).
GROUND_TRUTH_FILE = Path(__file__).parent.parent / "data" / "reviewed_ground_truth.csv"


def load_ground_truth(path: Path) -> List[Tuple[str, str, str]]:
    """Liest die Soll-Zuordnungen (login, erwartete BVDID oder Marker, Notiz) in Dateireihenfolge."""
    if not path.is_file():
        raise SystemExit(
            f"Soll-Zuordnungen nicht gefunden: {path}. Erwartet wird eine CSV-Datei mit der "
            "Kopfzeile login,expected,note."
        )
    with open(path, encoding="utf-8", newline="") as f:
        return [(row["login"], row["expected"], row["note"]) for row in csv.DictReader(f)]


# Komplexität bewusst akzeptiert (noqa statt Aufsplitten, analog zu
# run_pipeline() in orbis_matching_pipeline.py): einmaliges Verifikations-
# Skript, kein Teil der Produktions-Pipeline.
def main() -> int:  # noqa: C901
    ground_truth = load_ground_truth(GROUND_TRUTH_FILE)
    logins = [row[0] for row in ground_truth]
    expected = {row[0]: row[1] for row in ground_truth}
    notes = {row[0]: row[2] for row in ground_truth}

    config = ScoringConfig.from_yaml(load_matching_config())
    blacklist = load_blog_blacklist()
    organizations = fetch_organizations(limit=None, logins=logins)
    orgs_by_id = {o["id"]: o for o in organizations}

    domain_for_org = {}
    root_domain_for_org = {}
    for org in organizations:
        domain = extract_domain(org["blog"])
        if not domain or is_domain_blacklisted(domain, blacklist):
            continue
        domain_for_org[org["id"]] = domain
        root_domain_for_org[org["id"]] = extract_root_domain(domain) or domain

    client = OrbisClient()
    try:
        all_domains = list(set(domain_for_org.values()))
        domain_results = client.lookup_batch_by_websites_all_candidates(
            all_domains, root_domain_blacklist=blacklist
        )

        grouped_candidates = {}
        for org_id, domain in domain_for_org.items():
            raw = domain_results.get(domain, [])
            grouped_candidates[org_id] = build_match_candidates(
                orgs_by_id[org_id], raw, root_domain_for_org[org_id]
            )
        for org_id in orgs_by_id:
            grouped_candidates.setdefault(org_id, [])

        results = analyze_matches(grouped_candidates, config=config)

        pending_org_ids = [
            org_id for org_id, result in results.items() if result.status == MatchStatus.PENDING
        ]
        pending_bvd_ids = list(
            {c.bvd_id for org_id in pending_org_ids for c in results[org_id].all_candidates}
        )
        hq_subsidiary_counts = (
            client.lookup_hq_subsidiary_counts(pending_bvd_ids) if pending_bvd_ids else {}
        )

        hq_method_by_org = {}
        for org_id, result in results.items():
            if result.status != MatchStatus.PENDING:
                continue
            org = orgs_by_id[org_id]
            apply_hq_signals(
                result.all_candidates, root_domain_for_org.get(org_id), hq_subsidiary_counts
            )
            resolution = resolve_headquarters(
                result.all_candidates,
                github_name=org["name"] or None,
                github_login=org["login"],
                github_country=org["country_code"],
                min_name_similarity=config.name_similarity_min_score,
            )
            # Muss für JEDEN PENDING-Fall aufgerufen werden, auch wenn
            # resolution=None ist - siehe apply_hq_resolution_to_match_result()
            # in orbis_matching_pipeline.py (Fund: dieser Reset fehlte hier
            # bisher, die unbestätigte Rohschätzung aus analyze_matches()
            # blieb bei nicht bestätigten PENDING-Fällen fälschlich stehen).
            apply_hq_resolution_to_match_result(result, resolution)
            if resolution is None:
                continue
            hq_method_by_org[org_id] = resolution.hq_method

        print(
            f"{'Login':<20} {'Erwartet':<16} {'Pipeline':<16} {'Status':<12} {'Treffer?':<10} Notiz"
        )
        print("-" * 130)
        n_ok, n_total = 0, 0
        for login in logins:
            org = next((o for o in organizations if o["login"] == login), None)
            n_total += 1
            if org is None:
                print(f"{login:<20} {'(nicht in DB)':<16}")
                continue
            result = results.get(org["id"])
            exp = expected[login]
            if result is None or result.best_candidate is None:
                got_bvd = (
                    "NO_MATCH" if (result is None or result.status == MatchStatus.NO_MATCH) else "?"
                )
            else:
                got_bvd = result.best_candidate.bvd_id

            if exp in ("NONE_IN_LIST", "NO_MATCH_EXPECTED"):
                ok = got_bvd in ("NO_MATCH", "?") or result is None
            else:
                ok = got_bvd == exp

            n_ok += bool(ok)
            status = result.status.value if result else "no_org"
            method = hq_method_by_org.get(org["id"], "")
            mark = "OK" if ok else "ABWEICHUNG"
            print(
                f"{login:<20} {exp:<16} {got_bvd:<16} {status:<12} {mark:<10} "
                f"{method + ' | ' if method else ''}{notes[login]}"
            )

        print("-" * 130)
        print(f"{n_ok}/{n_total} stimmen mit der Dokumentation überein")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
