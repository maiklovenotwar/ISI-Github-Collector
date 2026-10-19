"""Orbis Matching Logik: Scoring, Konsens-Erkennung und Match-Klassifikation.

Dieses Modul konsolidiert die Matching-Logik, die bisher über zahlreiche
temporäre Skripte und SQL-Queries verstreut war (beides inzwischen entfernt).

Verantwortlichkeiten:
    - Name-Scoring basierend auf Namenslänge (konfigurierbar via YAML)
    - Blog/Website-Scoring (Domain-Match)
    - Kombinations-Bonus (Name + Blog)
    - Multi-Hit Konsens-Erkennung (unique / consensus / pending)
    - Match-Type Klassifikation (combined_high / combined_medium / combined_low)

Architektur:
    - Konfiguration wird aus config/orbis_matching.yaml geladen.
    - Reine Logik, keine DB-Abhängigkeit: Empfängt vorbereitete Dicts.
    - Arbeitet mit hq_detection.py zusammen für Phase 2 (Multi-Hit Resolution).

Datenfluss:
    GitHub-DB (geocodiert) → Export → Oracle Matching (SQL Views) → Scoring
    → Phase 1: unique/consensus → Phase 2: pending → HQ-Detection
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import yaml
from unidecode import unidecode

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG_PATH = Path(__file__).parent.parent.parent.parent / "config" / "orbis_matching.yaml"


def load_matching_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    """Lade die Matching-Konfiguration aus orbis_matching.yaml.

    Args:
        config_path: Optionaler Pfad zur YAML-Datei. Falls None, wird
            der Standard-Pfad (config/orbis_matching.yaml) verwendet.

    Returns:
        Dict mit der vollständigen Konfiguration.

    Raises:
        FileNotFoundError: Wenn die Konfigurationsdatei nicht existiert.
    """
    path = config_path or _DEFAULT_CONFIG_PATH

    if not path.exists():
        raise FileNotFoundError(
            f"Matching-Konfiguration nicht gefunden: {path}. "
            "Bitte config/orbis_matching.yaml erstellen."
        )

    with path.open("r", encoding="utf-8") as fh:
        config = yaml.safe_load(fh)

    logger.debug("Matching-Konfiguration geladen aus %s", path)
    return config


# ---------------------------------------------------------------------------
# Enums und Datenstrukturen
# ---------------------------------------------------------------------------


class MatchStatus(str, Enum):
    """Status einer Organization nach Multi-Hit Analyse."""

    UNIQUE = "unique"  # Genau 1 ORBIS-Treffer
    CONSENSUS = "consensus"  # Mehrere Treffer, alle gleiches Land
    PENDING = "pending"  # Mehrere Treffer, verschiedene Länder
    NO_MATCH = "no_match"  # Kein Treffer über Mindest-Score


class MatchType(str, Enum):
    """Klassifikation der Match-Qualität."""

    COMBINED_HIGH = "combined_high"  # Score >= 60
    COMBINED_MEDIUM = "combined_medium"  # Score 40-59
    COMBINED_LOW = "combined_low"  # Score 30-39
    NAME_ONLY = "name_only"  # Nur Name-Match
    LOGIN_FALLBACK = "login_fallback"  # Login als Fallback
    PENDING = "pending"  # Multi-Hit ohne Konsens


@dataclass
class ScoringConfig:
    """Scoring-Parameter aus orbis_matching.yaml, als typisierte Struktur.

    Defaults entsprechen den Werten in der aktuellen YAML-Datei.
    """

    min_score: int = 30
    # Eigene, niedrigere Schwelle für Namens-Fallback-Kandidaten (kein
    # Blog-Signal möglich) - siehe config/orbis_matching.yaml für Details.
    name_only_min_score: int = 10
    blog_score: int = 20
    combination_bonus: int = 10

    # Name-Gewichte nach Länge
    name_weight_very_long: int = 40  # >= 15 Zeichen
    name_weight_long: int = 30  # >= 11 Zeichen
    name_weight_medium: int = 20  # >= 7 Zeichen
    name_weight_short: int = 10  # < 7 Zeichen

    # Längen-Schwellwerte
    threshold_very_long: int = 15
    threshold_long: int = 11
    threshold_medium: int = 7

    # Match-Type Score-Schwellwerte
    high_score_threshold: int = 60
    medium_score_threshold: int = 40

    # Namens-Plausibilität (siehe interne Orbis-Notizen)
    name_similarity_min_score: float = 0.5
    generic_name_length_threshold: int = 5
    generic_name_malus: int = 15

    # Malus für Kandidaten, deren Namens-Ähnlichkeit NUR über Teilstring-
    # Enthaltensein zustande kam UND die keine weitere Bestätigung (Blog-
    # Match oder Länder-Übereinstimmung) haben - siehe score_candidate()
    # und Charge-11-Fund (Studecorators/Simon Data).
    containment_only_malus: int = 15

    @classmethod
    def from_yaml(cls, config: Dict[str, Any]) -> "ScoringConfig":
        """Erstelle ScoringConfig aus einem geladenen YAML-Dict."""
        scoring = config.get("scoring", {})
        name_weights = scoring.get("name_weights", {})
        thresholds = scoring.get("length_thresholds", {})
        match_types = config.get("match_types", {})
        plausibility = config.get("plausibility", {})

        return cls(
            min_score=scoring.get("min_score", cls.min_score),
            name_only_min_score=scoring.get("name_only_min_score", cls.name_only_min_score),
            blog_score=scoring.get("blog_score", cls.blog_score),
            combination_bonus=scoring.get("combination_bonus", cls.combination_bonus),
            name_weight_very_long=name_weights.get("very_long", cls.name_weight_very_long),
            name_weight_long=name_weights.get("long", cls.name_weight_long),
            name_weight_medium=name_weights.get("medium", cls.name_weight_medium),
            name_weight_short=name_weights.get("short", cls.name_weight_short),
            threshold_very_long=thresholds.get("very_long", cls.threshold_very_long),
            threshold_long=thresholds.get("long", cls.threshold_long),
            threshold_medium=thresholds.get("medium", cls.threshold_medium),
            high_score_threshold=match_types.get("combined_high", {}).get(
                "min_score", cls.high_score_threshold
            ),
            medium_score_threshold=match_types.get("combined_medium", {}).get(
                "min_score", cls.medium_score_threshold
            ),
            name_similarity_min_score=plausibility.get(
                "name_similarity_min_score", cls.name_similarity_min_score
            ),
            generic_name_length_threshold=plausibility.get(
                "generic_name_length_threshold", cls.generic_name_length_threshold
            ),
            generic_name_malus=plausibility.get("generic_name_malus", cls.generic_name_malus),
            containment_only_malus=plausibility.get(
                "containment_only_malus", cls.containment_only_malus
            ),
        )


@dataclass
class MatchCandidate:
    """Ein Match-Kandidat: GitHub-Org ↔ ORBIS-Eintrag mit Score."""

    github_id: int
    login: str
    match_value: str  # Der tatsächlich gematchte Wert (Name oder Blog)
    match_type_input: str  # 'name_blog', 'name_only', 'login_fallback'
    name_length: int = 0
    blog_domain: Optional[str] = None
    github_country: Optional[str] = None
    github_name: Optional[str] = None  # Roh-Anzeigename der GH-Org (für Namens-Ähnlichkeit)

    # ORBIS-Seite
    bvd_id: str = ""
    orbis_name: str = ""
    orbis_country: Optional[str] = None
    city: Optional[str] = None
    website: Optional[str] = None

    # Signal-Flags (werden beim Scoring gesetzt)
    has_name_match: bool = False
    has_blog_match: bool = False

    # Plausibilität (siehe filter_implausible_candidates)
    name_similarity: float = 0.0
    login_exact_match: bool = False
    # Ob name_similarity über den Teilstring-Enthaltensein-Zweig zustande kam
    # (siehe _calculate_name_similarity_detailed) - kann bei fehlender
    # weiterer Bestätigung reines Zufalls-Rauschen sein (Fund Charge 11:
    # "Studecorators" vs. "STUDEC"), siehe score_candidate()-Malus.
    name_similarity_via_containment: bool = False
    # Ob name_similarity über die lateinische Umschrift eines Orbis-Namens ohne
    # lateinische Buchstaben zustande kam (siehe name_similarity_details) - solche
    # Treffer werden eigens validiert (Entscheidung E19).
    name_via_transliteration: bool = False

    # Anzahl Filial-Rohdatensätze, die auf diesen Kandidaten dedupliziert wurden
    # (siehe select_branch_representative) - Proxy für "ist die aktive
    # Haupteinheit", nicht nur ein einzelner Nebeneintrag (Fund aus Charge 04)
    branch_count: int = 1

    # Scores
    name_score: int = 0
    blog_score: int = 0
    combination_bonus: int = 0
    total_score: int = 0

    # HQ-Auflösung (siehe hq_detection.py) - nachgelagertes, fachlich
    # eigenständiges Problem (siehe CONTEXT.md): Matching entscheidet die
    # Firma, HQ-Auflösung nur noch, welcher Orbis-Eintrag derselben Firma
    # die Zentrale ist. Lebten bis zum Merge von OrbisCandidate/MatchCandidate
    # auf einer separaten Dataclass in hq_detection.py, mit einer eigenen
    # Übersetzungsfunktion (scripts/enrichment/orbis_matching_pipeline.py) dazwischen -
    # hier zusammengeführt, weil in der Pipeline ohnehin bereits dieselben
    # Kandidaten-Objekte für beide Phasen verwendet werden. Werden vom Caller
    # NACH dem Matching zusätzlich befüllt (siehe
    # scripts/enrichment/orbis_matching_pipeline.py::apply_hq_signals()), nicht hier.
    is_guo_self: bool = False
    independence_indicator: Optional[str] = None
    guo_bvd_id: Optional[str] = None
    guo_country_iso: Optional[str] = None
    has_hq_entry: bool = False
    # Anzahl Töchter, die diese BVDID aktuell (HQ_CURRENT_INDICATOR='Current
    # info') als Hauptsitz nennen (siehe OrbisClient.lookup_hq_subsidiary_counts).
    # Bei mehreren has_hq_entry=True-Kandidaten Tiebreak-Signal: mehr Töchter
    # = stärkerer Beleg für die echte Konzernmutter.
    hq_subsidiary_count: int = 0
    date_incorporation: Optional[str] = None  # ISO date string
    operating_revenue: Optional[float] = None
    is_parent: bool = False
    consolidation_code: Optional[str] = None
    number_of_employees: Optional[int] = None
    # Root-Domain-Signal (vom Caller gesetzt: candidate.website == GH-Root-
    # Domain, exakter Root-Domain-Treffer, keine Pfad-/Subdomain-Variante -
    # siehe Orbis-Notizen (intern): batches/batch01_review.md,
    # Microsoft/Google-Fund: unter namensgleichen Kandidaten hat oft nur
    # EINER die echte Root-Domain, der Rest sind Hüllen/Auslandsniederlassungen
    # ohne Website)
    matches_root_domain: bool = False


@dataclass
class MatchResult:
    """Ergebnis der Match-Analyse für eine Organization."""

    github_id: int
    login: str
    status: MatchStatus
    # None nur bei status=NO_MATCH (kein Kandidat hat überhaupt die
    # Mindest-Score-Schwelle erreicht) - vorher stand hier fälschlich
    # MatchType.PENDING, obwohl "kein einziger Kandidat" fachlich etwas
    # anderes ist als "mehrere Kandidaten, unentschieden" (siehe CONTEXT.md,
    # Match-Status/Match-Type).
    match_type: Optional[MatchType] = None
    best_candidate: Optional[MatchCandidate] = None
    hit_count: int = 0
    country_count: int = 0
    consensus_country: Optional[str] = None
    all_candidates: List[MatchCandidate] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Namens-Plausibilität (muss vor JEDEM Signal laufen, nicht nur vor dem Scoring)
# ---------------------------------------------------------------------------
#
# Hintergrund: Bei domain-mehrfachgenutzten Firmen (github.com, openai.com, ...)
# kann selbst das höchstpriorisierte HQ-Signal (HQ_TABLE) auf Rauschen zeigen,
# weil Orbis' WEBSITE-Feld für viele unbeteiligte Firmen denselben String
# enthält. Ohne Namens-Plausibilitätsprüfung VOR jeder Signal-Auswertung wählt
# die Kaskade selbstbewusst falsch. Siehe
# Orbis-Notizen (intern): validation_batch2_baseline.md
# (Airbus/IBM: HQ_TABLE-Flag sitzt auf Rauschen, nicht auf der echten Zentrale).


def _normalize_for_similarity(value: str) -> str:
    """Normalisiere einen Namen für den Ähnlichkeitsvergleich (nur a-z/0-9)."""
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _calculate_name_similarity_detailed(
    orbis_name: str,
    github_name: Optional[str],
    github_login: str,
) -> tuple[float, bool]:
    """Wie `calculate_name_similarity()`, liefert zusätzlich ob die beste
    Ähnlichkeit über den Teilstring-Enthaltensein-Zweig zustande kam.

    Der Enthaltensein-Zweig hat einen festen Boden von 0.7 unabhängig davon,
    WARUM der kürzere String im längeren enthalten ist - das erfasst sowohl
    echte Abkürzungen ("SAP" in "SAP SE") als auch reinen Zufall ("studec"
    als literale ersten 6 Zeichen von "Studecorators", eigentlich "Stu"+
    "Decorators" - Fund aus Charge 11). Beide Fälle sind über reine
    Zeichenketten-Analyse nicht unterscheidbar (kein Leerzeichen im
    Orbis-Namen, das die Wortgrenze verraten würde) - das Flag erlaubt
    stattdessen einen gezielten Score-Malus für Enthaltensein-Treffer OHNE
    jede weitere Bestätigung (siehe `score_candidate()`), statt die Formel
    selbst zu verändern und dadurch echte Abkürzungsfälle wie Airbus/SAP zu
    riskieren.
    """
    if not orbis_name:
        return 0.0, False

    orbis_normalized = _normalize_for_similarity(orbis_name)
    candidates = [c for c in (github_name, github_login) if c]
    if not orbis_normalized or not candidates:
        return 0.0, False

    best_similarity = 0.0
    best_via_containment = False
    for candidate in candidates:
        candidate_normalized = _normalize_for_similarity(candidate)
        if not candidate_normalized:
            continue

        if candidate_normalized in orbis_normalized or orbis_normalized in candidate_normalized:
            shorter_len = min(len(candidate_normalized), len(orbis_normalized))
            longer_len = max(len(candidate_normalized), len(orbis_normalized))
            similarity = 0.7 + 0.3 * (shorter_len / longer_len)
            via_containment = shorter_len < longer_len
        else:
            similarity = SequenceMatcher(None, orbis_normalized, candidate_normalized).ratio()
            via_containment = False

        if similarity > best_similarity:
            best_similarity = similarity
            best_via_containment = via_containment

    return best_similarity, best_via_containment


_LATIN_LETTER = re.compile(r"[A-Za-z]")
# Rechtsformen in der Umschrift (unidecode) - sie tragen keinen Namensbestandteil
# und würden sonst Zufallstreffer erzeugen ("有限公司" -> "You Xian Gong Si").
_TRANSLITERATED_LEGAL_FORMS = re.compile(
    r"\b(?:gu fen you xian gong si|you xian ze ren gong si|you xian gong si|gong si|"
    r"zhu shi hui she|jusighoesa|yuhanhoesa|ooo|oao|zao|pao|ao)\b"
)
_MIN_TRANSLITERATED_LETTERS = 5
_MIN_SOURCE_CHARACTERS = 2


def _transliteration(orbis_name: str) -> Optional[str]:
    """Lateinische Umschrift eines Orbis-Namens ohne lateinische Buchstaben, sonst None.

    Ohne Umschrift fiele jeder solche Name am Namensfilter heraus (Befund M1). Damit
    kurze Silben keine Zufallstreffer erzeugen ("李" -> "Li" gegen "Li Lab"), werden
    Rechtsformen entfernt und ein Kern von mindestens fünf Buchstaben aus mindestens
    zwei Schriftzeichen verlangt. Grenze: Japanisch mit Kanji wird chinesisch gelesen
    ("株式会社メルカリ" -> 0,44 gegen "Mercari").
    """
    if not orbis_name or _LATIN_LETTER.search(orbis_name):
        return None
    if len(re.sub(r"\W", "", orbis_name)) < _MIN_SOURCE_CHARACTERS:
        return None
    text = _TRANSLITERATED_LEGAL_FORMS.sub(" ", unidecode(orbis_name).lower())
    if len(_normalize_for_similarity(text)) < _MIN_TRANSLITERATED_LETTERS:
        return None
    return text


def name_similarity_details(
    orbis_name: str,
    github_name: Optional[str],
    github_login: str,
    allow_transliteration: bool = True,
) -> Tuple[float, bool, bool]:
    """Ähnlichkeit, ob über Teilstring, ob über Umschrift (Maximum beider Varianten).

    Die beiden Flags gehören zur gewinnenden Variante, damit der Teilstring-Malus
    (`score_candidate()`) auch für umgeschriebene Namen greift.
    """
    similarity, via_containment = _calculate_name_similarity_detailed(
        orbis_name, github_name, github_login
    )
    transliterated = _transliteration(orbis_name) if allow_transliteration else None
    if transliterated:
        alt_similarity, alt_containment = _calculate_name_similarity_detailed(
            transliterated, github_name, github_login
        )
        if alt_similarity > similarity:
            return alt_similarity, alt_containment, True
    return similarity, via_containment, False


def calculate_name_similarity(
    orbis_name: str,
    github_name: Optional[str],
    github_login: str,
    allow_transliteration: bool = True,
) -> float:
    """Berechne die beste Namens-Ähnlichkeit zwischen Orbis-Name und GH Name/Login.

    Prüft gegen BEIDE GitHub-Signale (Anzeigename und Login) und nimmt das
    Maximum, da der Login manchmal präziser ist als der Anzeigename (Fund aus
    Charge 04, Fall "touchmedia360" vs. Anzeigename "Touch Media" -
    siehe Orbis-Notizen (intern): batches/batch04_review.md).

    Enthält eine Substring-Sonderbehandlung: reine SequenceMatcher-Ratio
    unterschätzt sehr häufige, echte Fälle wie "SAP" vs. "SAP SE" oder "Acme"
    vs. "Acme Corporation" (der kürzere String ist vollständig im längeren
    enthalten, meist weil eine Rechtsform angehängt ist) - ohne diese
    Sonderbehandlung würden viele echte Treffer fälschlich als unplausibel
    verworfen. Nutzt difflib (Stdlib) statt einer zusätzlichen
    Fuzzy-Matching-Abhängigkeit.

    Args:
        orbis_name: NAME_NATIVE des Orbis-Kandidaten.
        github_name: Anzeigename der GitHub-Org, falls vorhanden.
        github_login: Login der GitHub-Org (immer vorhanden).
        allow_transliteration: Umschrift nicht-lateinischer Orbis-Namen zulassen
            (siehe name_similarity_details); der Namens-Rückfall schaltet sie ab.

    Returns:
        Ähnlichkeit zwischen 0.0 und 1.0.
    """
    similarity, _, _ = name_similarity_details(
        orbis_name, github_name, github_login, allow_transliteration
    )
    return similarity


def has_exact_login_substring(
    orbis_name: str,
    github_login: str,
    min_login_length: int = 5,
) -> bool:
    """Prüfe, ob der komplette GH-Login wortwörtlich im Orbis-Namen steckt.

    Anders als `calculate_name_similarity()` (Fuzzy-Ähnlichkeit, nimmt das
    Maximum über Name UND Login) ist dies ein strenger Substring-Test nur
    gegen den Login. Fund aus dem Touch-Media-Fall (Charge 04):
    Login "touchmedia360" steckt exakt in "Touch Media 360 Services SRL",
    aber nicht in "Touch Media SRL" - während Letzteres beim generischen
    Namensvergleich sogar höher abschneidet (kürzerer, "saubererer" Treffer
    gegen den Anzeigenamen "Touch Media"). Ein seltenes, spezifisches Token
    wie "360" im Login ist ein stärkeres Signal als generische
    Namens-Ähnlichkeit und sollte diese bei Gleichstand im Score übertrumpfen.
    `min_login_length` verhindert Trivial-Treffer bei kurzen/generischen
    Logins (analog zu `generic_name_length_threshold`).

    WICHTIG (Fund aus Charge 11, "otoci"-Fall): wenn Login und Orbis-Name
    NORMALISIERT IDENTISCH sind (z.B. Login "otoci" == Orbis-Name "Otoci",
    kroatisch für "Inseln", ein reiner Zufallstreffer auf ein Alltagswort),
    ist das trivial "enthalten" (ein String ist immer Substring seiner
    selbst), liefert aber KEINE zusätzliche Spezifität gegenüber der
    ohnehin schon berechneten Namens-Ähnlichkeit (die dann ohnehin 1.0
    beträgt). Der eigentliche Sinn dieser Funktion ist, ein seltenes,
    spezifisches Login-TOKEN INNERHALB eines LÄNGEREN Namens zu erkennen
    (wie "360" in "touchmedia360" vs. "Touch Media 360 Services SRL") - das
    erfordert echte Gleichheit auszuschließen, sonst höhlt dieser
    Escape-Hatch den PENDING-Schutz für Kurz-Namen-Zufallstreffer
    (`determine_match_status()`) komplett aus.

    Args:
        orbis_name: Firmenname aus Orbis.
        github_login: Login der GitHub-Org.
        min_login_length: Mindestlänge des normalisierten Logins, ab der der
            Test überhaupt greift.

    Returns:
        True, wenn der normalisierte Login als ECHTER (nicht-identischer)
        Substring im normalisierten Orbis-Namen vorkommt und lang genug ist,
        um spezifisch zu sein.
    """
    if not orbis_name or not github_login:
        return False
    login_normalized = _normalize_for_similarity(github_login)
    if len(login_normalized) < min_login_length:
        return False
    orbis_normalized = _normalize_for_similarity(orbis_name)
    if login_normalized == orbis_normalized:
        return False
    return login_normalized in orbis_normalized


def filter_implausible_candidates(
    candidates: Sequence[MatchCandidate],
    min_similarity: float = 0.5,
) -> List[MatchCandidate]:
    """Entferne Kandidaten ohne plausible Namens-Ähnlichkeit zu Name/Login.

    Muss laufen, BEVOR irgendein Scoring- oder HQ-Signal ausgewertet wird -
    nicht nur vor der Domain-Bewertung. Setzt `name_similarity` auf jedem
    Kandidaten (auch auf durchgelassenen), damit nachgelagerte Signale (z.B.
    Generic-Name-Malus) den Wert weiterverwenden können.

    Kandidaten ganz ohne `orbis_name` werden durchgelassen (nicht beurteilbar),
    aber das ist in der Praxis selten, da Orbis-Abfragen NAME_NATIVE immer
    mitliefern.

    Args:
        candidates: Alle Kandidaten einer Organization.
        min_similarity: Mindest-Ähnlichkeit (0.0-1.0).

    Returns:
        Gefilterte Liste; Reihenfolge bleibt erhalten.
    """
    plausible: List[MatchCandidate] = []
    for c in candidates:
        (
            c.name_similarity,
            c.name_similarity_via_containment,
            c.name_via_transliteration,
        ) = name_similarity_details(c.orbis_name, c.github_name, c.login)
        c.login_exact_match = has_exact_login_substring(c.orbis_name, c.login)

        if not c.orbis_name:
            plausible.append(c)
            continue

        if c.name_similarity >= min_similarity:
            plausible.append(c)
        else:
            logger.debug(
                "Kandidat verworfen (Namens-Ähnlichkeit %.2f < %.2f): "
                "Orbis '%s' vs. GH Name='%s' Login='%s' (BVDID=%s)",
                c.name_similarity,
                min_similarity,
                c.orbis_name,
                c.github_name,
                c.login,
                c.bvd_id,
            )

    return plausible


# ---------------------------------------------------------------------------
# BVDID-Filial-Deduplizierung
# ---------------------------------------------------------------------------
#
# Orbis kennzeichnet Filial-/Branch-Datensätze mit Suffixen an derselben
# Basis-BVDID (z.B. "-B0001", "-1002"). Ohne Dedup zählt jede Filiale als
# eigener "Hit", was echte Multi-Standort-Firmen fälschlich als Multi-Hit
# ohne Konsens (PENDING) erscheinen lässt UND das HAS_HQ-Flag bevorzugt auf
# Filialen statt der Hauptentität sitzt (Airbus "-B0001"/"-B0002",
# numerische Suffixe wie "-1002" - siehe
# Orbis-Notizen (intern): batches/batch04_review.md).

_BVD_BRANCH_SUFFIX_PATTERN = re.compile(r"-(?:B\d+|\d{3,5})$")


def normalize_bvd_id_base(bvd_id: str) -> str:
    """Entferne einen bekannten Filial-/Branch-Suffix von einer BVD-ID.

    Args:
        bvd_id: Die rohe BVD-ID, ggf. mit Filial-Suffix.

    Returns:
        Die Basis-BVD-ID ohne Filial-Suffix (unverändert, falls kein
        erkannter Suffix vorliegt).
    """
    if not bvd_id:
        return bvd_id
    return _BVD_BRANCH_SUFFIX_PATTERN.sub("", bvd_id)


def select_branch_representative(candidates: Sequence[MatchCandidate]) -> List[MatchCandidate]:
    """Reduziere Filial-Duplikate (gleiche Basis-BVDID) auf einen Repräsentanten.

    Bevorzugt die unsuffigierte Variante als Repräsentant, weil das
    HAS_HQ-Flag empirisch unzuverlässig eher auf Filial-Suffixen sitzt als
    auf der Hauptentität. Erfordert, dass `total_score` bereits berechnet ist
    (Fallback-Tiebreak, falls keine unsuffigierte Variante vorliegt).

    Args:
        candidates: Kandidaten, ggf. mit mehreren Filial-Duplikaten je Firma.

    Returns:
        Liste mit maximal einem Kandidaten pro Basis-BVDID. Reihenfolge nicht
        garantiert stabil (Gruppierung über Dict).
    """
    groups: Dict[str, List[MatchCandidate]] = {}
    for c in candidates:
        base = normalize_bvd_id_base(c.bvd_id)
        groups.setdefault(base, []).append(c)

    representatives: List[MatchCandidate] = []
    for base, group in groups.items():
        if len(group) == 1:
            representatives.append(group[0])
            continue

        unsuffixed = [c for c in group if c.bvd_id == base]
        representative = unsuffixed[0] if unsuffixed else max(group, key=lambda c: c.total_score)
        representative.branch_count = len(group)
        representatives.append(representative)

    return representatives


# ---------------------------------------------------------------------------
# Scoring-Logik
# ---------------------------------------------------------------------------


def calculate_name_score(name_length: int, config: ScoringConfig) -> int:
    """Berechne den Name-Score basierend auf der Namenslänge.

    Längere Namen sind spezifischer und ergeben einen höheren Score.
    Schwellwerte und Gewichte kommen aus der Konfiguration.

    Args:
        name_length: Länge des Organisationsnamens.
        config: Scoring-Konfiguration.

    Returns:
        Name-Score (0 bis name_weight_very_long).
    """
    if name_length >= config.threshold_very_long:
        return config.name_weight_very_long
    if name_length >= config.threshold_long:
        return config.name_weight_long
    if name_length >= config.threshold_medium:
        return config.name_weight_medium
    return config.name_weight_short


def score_candidate(
    candidate: MatchCandidate,
    config: ScoringConfig,
) -> MatchCandidate:
    """Berechne den Gesamt-Score für einen Match-Kandidaten.

    Setzt name_score, blog_score, combination_bonus und total_score
    direkt auf dem Kandidaten. Wendet zusätzlich einen Malus für generische/
    sehr kurze Namen ohne Blog-Bestätigung an (Fund aus Charge 05-09: Namen
    wie "FALS"/"F3X" liefern über Präfixsuche nur zufällige, unbeteiligte
    Treffer - siehe Orbis-Notizen (intern): batches/
    batch05-09_review.md).

    Args:
        candidate: Der zu bewertende Match-Kandidat.
        config: Scoring-Konfiguration.

    Returns:
        Der gleiche Kandidat (mutiert) mit gesetzten Score-Feldern.
    """
    candidate.name_score = (
        calculate_name_score(candidate.name_length, config) if candidate.has_name_match else 0
    )

    candidate.blog_score = config.blog_score if candidate.has_blog_match else 0

    candidate.combination_bonus = (
        config.combination_bonus if candidate.has_name_match and candidate.has_blog_match else 0
    )

    generic_name_malus = 0
    if (
        candidate.has_name_match
        and not candidate.has_blog_match
        and 0 < candidate.name_length < config.generic_name_length_threshold
    ):
        generic_name_malus = config.generic_name_malus

    # Fund Charge 11 (Studecorators/Simon Data): eine über Teilstring-
    # Enthaltensein zustande gekommene Namens-Ähnlichkeit kann reiner Zufall
    # sein (kein Leerzeichen im Orbis-Namen verrät eine echte Wortgrenze -
    # "studec" sind zufällig die ersten 6 Zeichen von "Studecorators",
    # eigentlich "Stu"+"Decorators"). Ohne jede weitere Bestätigung (Blog
    # oder Land) bekommt so ein Kandidat einen Malus statt vollem Vertrauen.
    # Bewusst NICHT die Ähnlichkeits-Formel selbst verändert, um echte
    # Abkürzungsfälle wie "Airbus"/"SAP" nicht zu riskieren.
    country_confirms = bool(
        candidate.github_country
        and candidate.orbis_country
        and candidate.github_country == candidate.orbis_country
    )
    containment_only_malus = 0
    if (
        candidate.name_similarity_via_containment
        and not candidate.has_blog_match
        and not country_confirms
    ):
        containment_only_malus = config.containment_only_malus

    candidate.total_score = max(
        0,
        candidate.name_score
        + candidate.blog_score
        + candidate.combination_bonus
        - generic_name_malus
        - containment_only_malus,
    )

    return candidate


# ---------------------------------------------------------------------------
# Match-Type Klassifikation
# ---------------------------------------------------------------------------


def classify_match_type(candidate: MatchCandidate, config: ScoringConfig) -> MatchType:
    """Klassifiziere einen Match basierend auf Herkunft und Gesamt-Score.

    `name_only`- und `login_fallback`-Kandidaten haben laut
    config/orbis_matching.yaml keine eigenen Score-Stufen (kein `min_score`
    dort definiert) - sie werden direkt anhand von `match_type_input`
    klassifiziert, nicht über die combined_*-Score-Schwellen. Vorher wurden
    diese Kandidaten hier fälschlich anhand des (für sie irrelevanten)
    `min_score` bewertet und fielen dadurch auf `PENDING`, obwohl
    `MatchType.NAME_ONLY`/`LOGIN_FALLBACK` genau für sie vorgesehen sind
    (Fund: leere Kandidaten im Enum, siehe CONTEXT.md).

    Args:
        candidate: Der zu klassifizierende Match-Kandidat (bereits gescort).
        config: Scoring-Konfiguration mit den Schwellwerten.

    Returns:
        MatchType-Enum.
    """
    if candidate.match_type_input == "name_only":
        return MatchType.NAME_ONLY
    if candidate.match_type_input == "login_fallback":
        return MatchType.LOGIN_FALLBACK

    total_score = candidate.total_score
    if total_score >= config.high_score_threshold:
        return MatchType.COMBINED_HIGH
    if total_score >= config.medium_score_threshold:
        return MatchType.COMBINED_MEDIUM
    if total_score >= config.min_score:
        return MatchType.COMBINED_LOW
    return MatchType.PENDING


# ---------------------------------------------------------------------------
# Konsens-Erkennung (Multi-Hit Analyse)
# ---------------------------------------------------------------------------


def meets_min_score(
    candidate: MatchCandidate,
    min_score: int,
    name_only_min_score: Optional[int] = None,
) -> bool:
    """Prüfe einen Kandidaten gegen die für ihn passende Mindest-Score-Schwelle.

    Namens-Fallback-Kandidaten (`match_type_input == "name_only"` ODER
    `"login_fallback"`) haben strukturell nie ein Blog-Signal
    (blog_score/combination_bonus immer 0) und brauchen deshalb eine eigene,
    niedrigere Schwelle (`name_only_min_score`) statt des für Domain+Name
    kalibrierten `min_score` - siehe config/orbis_matching.yaml. Beide teilen
    sich denselben Score-Aufbau (nur Name- oder nur Login-Score, siehe
    build_name_only_candidates() in scripts/enrichment/orbis_matching_pipeline.py), also
    auch dieselbe Schwelle.

    Args:
        candidate: Der zu prüfende Kandidat.
        min_score: Standard-Mindest-Score (Domain+Name-Kalibrierung).
        name_only_min_score: Mindest-Score für Namens-/Login-Fallback-
            Kandidaten. Falls None, wird für alle Kandidaten `min_score`
            verwendet (Rückwärtskompatibilität).

    Returns:
        True, wenn der Kandidat die für ihn geltende Schwelle erreicht.
    """
    if name_only_min_score is not None and candidate.match_type_input in (
        "name_only",
        "login_fallback",
    ):
        return candidate.total_score >= name_only_min_score
    return candidate.total_score >= min_score


def determine_match_status(
    candidates: Sequence[MatchCandidate],
    min_score: int,
    name_only_min_score: Optional[int] = None,
) -> MatchStatus:
    """Bestimme den Match-Status für eine Gruppe von Kandidaten.

    Args:
        candidates: Alle Kandidaten einer Organization.
        min_score: Mindest-Score aus der Konfiguration.
        name_only_min_score: Eigene, niedrigere Schwelle für
            Namens-Fallback-Kandidaten (siehe `meets_min_score()`).

    Returns:
        MatchStatus (unique / consensus / pending / no_match).
    """
    # Filtere nach (kandidatenspezifischer) Mindest-Score
    valid = [c for c in candidates if meets_min_score(c, min_score, name_only_min_score)]

    if not valid:
        return MatchStatus.NO_MATCH

    # Fund aus Charge 11 (Orbis-Notizen (intern): batches/
    # batch11_review.md): reine Namens-/Login-Fallback-Treffer ohne jede
    # weitere Bestätigung (kein Blog-Match, kein exakter Login-Substring)
    # dürfen nie UNIQUE/CONSENSUS erreichen - selbst wenn zufällig nur ein
    # Kandidat bzw. nur ein Land vorliegt. Ein einzelner Zufallstreffer auf
    # ein generisches Wort (z.B. "Otoci"/"Beholder", zufällig namensgleich
    # mit dem GH-Login) hat sonst schlicht keine Konkurrenz, die die
    # Mehrdeutigkeit sichtbar machen würde, und wirkte dadurch fälschlich
    # "eindeutig". `login_exact_match` bleibt als Ausnahme zulässig - ein
    # seltenes/spezifisches Login-Token im Namen ist ein eigenständig
    # starkes Signal (siehe has_exact_login_substring()). Gilt für
    # "login_fallback" genau wie für "name_only" (siehe meets_min_score()) -
    # beide sind strukturell dasselbe unbestätigte Rauschrisiko, nur mit
    # anderem Suchbegriff.
    if all(
        c.match_type_input in ("name_only", "login_fallback") and not c.login_exact_match
        for c in valid
    ):
        return MatchStatus.PENDING

    unique_bvdids = {c.bvd_id for c in valid}
    unique_countries = {c.orbis_country for c in valid if c.orbis_country}

    if len(unique_bvdids) == 1:
        return MatchStatus.UNIQUE

    if len(unique_countries) == 1:
        return MatchStatus.CONSENSUS

    return MatchStatus.PENDING


# ---------------------------------------------------------------------------
# Haupt-Analysefunktion
# ---------------------------------------------------------------------------


def analyze_matches(
    grouped_candidates: Dict[int, List[MatchCandidate]],
    config: Optional[ScoringConfig] = None,
) -> Dict[int, MatchResult]:
    """Analysiere Matches für eine Gruppe von Organizations.

    Führt Scoring, Konsens-Erkennung und Match-Type Klassifikation
    für jede Organization durch.

    Args:
        grouped_candidates: Dict mit github_id als Schlüssel und Liste
            von MatchCandidate als Wert. Kandidaten müssen bereits die
            Flags has_name_match und has_blog_match gesetzt haben.
        config: Optionale Scoring-Konfiguration. Falls None, werden
            die Standard-Defaults verwendet.

    Returns:
        Dict mit github_id und dem jeweiligen MatchResult.
    """
    if config is None:
        config = ScoringConfig()

    results: Dict[int, MatchResult] = {}

    for github_id, candidates in grouped_candidates.items():
        # 1. Namens-Plausibilität VOR jedem Scoring/Signal prüfen (muss vor
        #    HQ-Signalen laufen, siehe Modul-Docstring bei filter_implausible_candidates)
        plausible = filter_implausible_candidates(candidates, config.name_similarity_min_score)

        # 2. Scoring
        for c in plausible:
            score_candidate(c, config)

        # 3. Filial-Duplikate auf einen Repräsentanten je Basis-BVDID reduzieren
        deduped = select_branch_representative(plausible)

        # 4. Konsens-Erkennung
        status = determine_match_status(deduped, config.min_score, config.name_only_min_score)

        # 5. Besten Kandidaten bestimmen
        # Tie-Break bei gleichem total_score, in absteigender Spezifität:
        #   a) login_exact_match: der volle Login steckt wortwörtlich im
        #      Namen (seltenes/spezifisches Token wie "360" bei Touch Media -
        #      schlägt generische Namens-Ähnlichkeit, siehe has_exact_login_substring)
        #   b) branch_count: wie viele Filial-Rohdatensätze auf diesen
        #      Kandidaten dedupliziert wurden (Proxy für "aktive Haupteinheit",
        #      geprüfter Fall: die Hauptentität hatte deutlich mehr Filial-Datensätze
        #      als eine Variante derselben Firma)
        #   c) name_similarity, dann BVDID als letzter, willkürlicher Fallback
        valid = [
            c for c in deduped if meets_min_score(c, config.min_score, config.name_only_min_score)
        ]
        valid.sort(
            key=lambda c: (
                -c.total_score,
                -int(c.login_exact_match),
                -c.branch_count,
                -c.name_similarity,
                c.bvd_id,
            )
        )

        best = valid[0] if valid else None

        # 6. Match-Type Klassifikation
        if status == MatchStatus.PENDING:
            match_type = MatchType.PENDING
        elif best:
            match_type = classify_match_type(best, config)
        else:
            # status ist hier zwangsläufig NO_MATCH (best ist None genau
            # dann, siehe meets_min_score()-Filterung oben) - kein Kandidat
            # zu klassifizieren, also kein Match-Type statt des fälschlich
            # wiederverwendeten PENDING.
            match_type = None

        # 7. Konsens-Land
        countries = {c.orbis_country for c in valid if c.orbis_country}
        consensus_country = countries.pop() if len(countries) == 1 else None

        login = candidates[0].login if candidates else ""

        results[github_id] = MatchResult(
            github_id=github_id,
            login=login,
            status=status,
            match_type=match_type,
            best_candidate=best,
            hit_count=len({c.bvd_id for c in valid}),
            country_count=len(countries) if len(countries) != 1 else 1,
            consensus_country=consensus_country,
            all_candidates=valid,
        )

    # Statistiken loggen
    status_counts = _count_by_attr(results.values(), "status")
    type_counts = _count_by_attr(results.values(), "match_type")
    total = len(results)

    logger.info("Match-Analyse abgeschlossen: %d Organizations", total)
    for status_val, count in sorted(status_counts.items()):
        pct = count / total * 100 if total else 0
        logger.info("  Status %s: %d (%.1f%%)", status_val, count, pct)
    for type_val, count in sorted(type_counts.items()):
        pct = count / total * 100 if total else 0
        logger.info("  Type   %s: %d (%.1f%%)", type_val, count, pct)

    return results


def _count_by_attr(items: Any, attr: str) -> Dict[str, int]:
    """Zähle Vorkommen nach Attribut-Wert."""
    counts: Dict[str, int] = {}
    for item in items:
        val = str(getattr(item, attr, "unknown"))
        counts[val] = counts.get(val, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Country-Code Adjustment (Bonus/Malus aus temp_combined_scoring_strategy.py)
# ---------------------------------------------------------------------------


def calculate_country_code_adjustment(
    github_cc: Optional[str],
    headquarters_cc: Optional[str],
    all_orbis_countries: Optional[set] = None,
) -> int:
    """Berechne den Country-Code Bonus/Malus.

    Args:
        github_cc: Country-Code der GitHub-Organization (aus Geocoding).
        headquarters_cc: Country-Code des ermittelten Hauptsitzes.
        all_orbis_countries: Menge aller ORBIS-Länder für diese Org.

    Returns:
        Adjustment-Wert: +15 (Match), +5 (teilweise), -5 (kein Match), 0 (unbekannt).
    """
    if not github_cc:
        return 0

    if github_cc == headquarters_cc:
        return +15

    if all_orbis_countries and github_cc in all_orbis_countries:
        return +5

    return -5
