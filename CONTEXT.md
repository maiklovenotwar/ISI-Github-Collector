# Orbis Matching

Matches GitHub organizations to companies in the Orbis company database to obtain
additional firmographic data (notably country and headquarters location).

## Terminology

**Match-Status**:
Result of the ambiguity check for an organization: how many plausible Orbis
candidates exist, and whether they agree. Values: `unique` (exactly one
candidate), `consensus` (multiple candidates, same country), `pending`
(multiple disagreeing candidates, or an unconfirmed name/login match with no
corroborating signal — deliberately flagged as "not confident enough" rather
than guessed), `no_match` (no candidate reaches the minimum threshold).
A `pending` organization receives a result only if the Headquarters
Resolution finds a candidate corroborated by country or domain; the status
stays `pending` and is stored with the result.
_Avoid_: Confidence, Score (see Match-Type).

**Match-Type**:
Classification of how the selected candidate was found, and at what score
tier — independent of Match-Status. Values: `combined_high` / `combined_medium`
/ `combined_low` (name and/or blog signal, tiered by score), `name_only`
(found via company name only, no blog signal available), `login_fallback`
(found via the GitHub login because no name was available). A Match-Status of
`no_match` has no candidate to classify, and therefore no Match-Type.
_Avoid_: Do not conflate with Match-Status — a `unique` status can still have
`name_only` as its Match-Type; the two axes are independent. A resolved
`pending` organization carries the Match-Type of the chosen candidate. Do not use as a
substitute for `no_match` either (no candidate is not the same as an
ambiguous candidate).

### Candidate Stages

A GitHub organization passes through three progressively filtered stages of
possible Orbis matches:

**Candidate**:
Any raw GitHub-organization/Orbis-company pairing as returned by the
database query — not yet evaluated.
_Avoid_: Hit, Match.

**Contender** (German codebase term: `Anwärter`):
A candidate that has passed the name-plausibility filter (sufficient name
similarity to the GitHub name/login), but whose score threshold has not yet
been checked.
_Avoid_: Hit, "plausible candidate".

**Hit** (German codebase term: `Treffer`):
A contender that has additionally reached the minimum score threshold, and
therefore counts toward Match-Status and consensus detection.
_Avoid_: Candidate, Contender.

### Headquarters Resolution

A conceptually separate, downstream problem: matching decides which company
belongs to a GitHub organization; headquarters resolution then decides which
of several Orbis entries is chosen. In practice the entries of a `pending`
organization are not always entries of the same company: the resolution also
separates name collisions between different companies, which is why every
choice needs a corroboration by country or domain.

**Headquarters**:
The actual corporate headquarters of an already-matched company — a fact
about the real world, not a result produced by the code.
_Avoid_: Headquarters Resolution (that is the procedure that estimates it,
not the headquarters itself).

**Headquarters Resolution**:
The procedure that, given multiple Orbis entries for the same (already
matched) company, produces a headquarters estimate via a prioritized signal
cascade, together with the method that produced it. Carries a known,
non-eliminable error rate; its result is an estimate, not a verified fact.
_Avoid_: Headquarters (do not equate the two — resolution is an
approximation, not a fact).

## A Note on Terminology in the Codebase

Source code comments, docstrings, and some script/variable names still use
the original German terms (`Kandidat`, `Anwärter`, `Treffer`, `Hauptsitz`,
`HQ-Auflösung`); they map one-to-one onto Candidate, Contender, Hit,
Headquarters, and Headquarters Resolution above.
