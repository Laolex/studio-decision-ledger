#!/usr/bin/env python3
"""Generate the synthetic evidence and policy dataset as deterministic SQL.

Emits `db/seed.sql`. No database connection is required to run this, which
keeps the data layer reviewable without standing infrastructure up first.

Deliberately seeds EVIDENCE AND POLICY ONLY. Decision records are not seeded:
they must be produced by the real decision path so that a reviewer can see the
pipeline create them rather than take our word for it. See README, "Bootstrap
the demo decisions".

The dataset is entirely fictional. `North Star` is not a real title and none of
the rights, clearance, rating or delivery facts describe a real contractual or
regulatory condition in any territory.

Three revisions tell the whole product story:

  rev 1  (2026-07-01)  Clean. Everything clears for Nigeria.
  rev 2  (2026-08-05)  The music sync window is corrected: it ends 2026-07-31,
                       not 2027-06-01. Business time moves; the past does not.
  rev 3  (2026-08-06)  The Nigeria grant is restated as AVOD-only, BACKDATED to
                       the original commencement. This is the important one:
                       it changes what the answer would have been on a date
                       already decided. A decision pinned at rev 1 must still
                       replay as AVAILABLE, and the comparison view must be
                       able to say the correction would have produced HOLD
                       without touching the original record.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

TITLE = "NORTHSTAR-S01E06"
TERRITORY = "NG"

# --- policy -----------------------------------------------------------------

POLICY_REVISION = "POL-2026.07"
POLICY_EFFECTIVE_AT = "2026-07-01 00:00:00.000"
POLICY_RECORDED_AT = "2026-06-24 14:10:00.000"

# Rules are data, not code, so that a decision can bind to a hash of them.
# Ordering is significant: the first matching blocking rule is the reported
# cause, which keeps the "exact blocking condition" in the UI stable.
POLICY_RULES = {
    "policy_revision": POLICY_REVISION,
    "release_path": "SVOD",
    "rules": [
        {
            "id": "LIC-001",
            "requires": "an active licence covering the territory and effective date",
            "outcome_when_unmet": "HOLD",
        },
        {
            "id": "LIC-002",
            "requires": "the licence rights scope to include the release path",
            "outcome_when_unmet": "HOLD",
        },
        {
            "id": "CLR-001",
            "requires": "every clearance of a mandatory kind to be active on the effective date",
            "mandatory_kinds": ["MUSIC_SYNC", "MUSIC_MASTER", "STOCK_FOOTAGE", "TALENT"],
            "outcome_when_unmet": "HOLD",
        },
        {
            "id": "RTG-001",
            "requires": "a valid, unexpired rating certificate for the territory",
            "outcome_when_unmet": "HOLD",
        },
        {
            "id": "DLV-001",
            "requires": "final delivery approval with captions approved",
            "outcome_when_unmet": "HOLD",
        },
        {
            "id": "CNT-001",
            "requires": "no continuity exception of BLOCKING severity left OPEN",
            "outcome_when_unmet": "HOLD",
        },
        {
            "id": "ESC-001",
            "requires": "facts for every enabled rule to be present and non-contradictory",
            "outcome_when_unmet": "ESCALATE",
        },
    ],
}


# POL-2026.08 adds the synthetic-content rules. POL-2026.07 is left byte-for-byte
# alone: decisions already recorded against it store its hash, and editing the
# document in place would fail their replay for a reason unrelated to their
# evidence. A policy revision is an immutable artifact, not a mutable setting.
POLICY_REVISION_V2 = "POL-2026.08"
POLICY_EFFECTIVE_AT_V2 = "2026-08-17 00:00:00.000"
POLICY_RECORDED_AT_V2 = "2026-08-16 18:00:00.000"

POLICY_RULES_V2 = {
    **POLICY_RULES,
    "policy_revision": POLICY_REVISION_V2,
    "rules": POLICY_RULES["rules"] + [
        {
            "id": "SYN-001",
            "requires": (
                "a performer consent record to exist where an asset is recorded "
                "as SYNTHETIC or ASSISTED"
            ),
            "outcome_when_unmet": "ESCALATE",
        },
        {
            "id": "CON-001",
            "requires": (
                "the performer consent on file to cover the territory, the "
                "effective date, and the likeness scope"
            ),
            "outcome_when_unmet": "HOLD",
        },
    ],
}


def canonical_json(payload: dict) -> str:
    """Stable serialization. The hash binding depends on this being exact."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- evidence ---------------------------------------------------------------

# (license_id, territory, scope, valid_from, valid_to, status, revision, recorded_at, note)
LICENSES = [
    (
        "LIC-NG-0091", TERRITORY, "SVOD",
        "2026-06-01 00:00:00.000", "2027-06-01 00:00:00.000", "ACTIVE",
        1, "2026-07-01 09:00:00.000", "",
    ),
    (
        "LIC-NG-0091", TERRITORY, "AVOD",
        "2026-06-01 00:00:00.000", "2027-06-01 00:00:00.000", "ACTIVE",
        3, "2026-08-06 16:45:00.000",
        "Territory grant restated as AVOD-only per amendment 3; applies from original commencement.",
    ),
]

# (clearance_id, asset_ref, kind, valid_from, valid_to, status, revision, recorded_at, note)
CLEARANCES = [
    (
        "CLR-MS-0031", "Midnight Drive", "MUSIC_SYNC",
        "2026-06-01 00:00:00.000", "2027-06-01 00:00:00.000", "ACTIVE",
        1, "2026-07-01 09:00:00.000", "",
    ),
    (
        "CLR-MS-0031", "Midnight Drive", "MUSIC_SYNC",
        "2026-06-01 00:00:00.000", "2026-07-31 00:00:00.000", "ACTIVE",
        2, "2026-08-05 11:20:00.000",
        "Sync term corrected against executed cue sheet; window ends 2026-07-31.",
    ),
    (
        "CLR-MM-0032", "Midnight Drive", "MUSIC_MASTER",
        "2026-06-01 00:00:00.000", "2027-06-01 00:00:00.000", "ACTIVE",
        1, "2026-07-01 09:00:00.000", "",
    ),
    (
        "CLR-TL-0033", "Ensemble cast — episode 6", "TALENT",
        "2026-05-15 00:00:00.000", "2029-05-15 00:00:00.000", "ACTIVE",
        1, "2026-07-01 09:00:00.000", "",
    ),
    (
        "CLR-SF-0034", "Aerial plate, harbour at dusk", "STOCK_FOOTAGE",
        "2026-05-15 00:00:00.000", "2031-05-15 00:00:00.000", "ACTIVE",
        1, "2026-07-01 09:00:00.000", "",
    ),
]

# (rating_id, rating_code, issued_at, expires_at, status, revision, recorded_at, note)
RATINGS = [
    (
        "RTG-NG-0007", "15", "2026-05-20 00:00:00.000", "2028-05-20 00:00:00.000",
        "VALID", 1, "2026-07-01 09:00:00.000", "",
    ),
]

# (delivery_id, master_version, approved_at, captions, audio_description, revision, recorded_at, note)
DELIVERIES = [
    (
        "DLV-0004", "v1.2", "2026-05-28 17:30:00.000", "APPROVED", "APPROVED",
        1, "2026-07-01 09:00:00.000", "",
    ),
]

# (exception_id, scene_ref, severity, state, resolution_ref, revision, recorded_at, note)
CONTINUITY = [
    (
        "EXC-0012", "Sc. 41 — wardrobe, grey coat", "ADVISORY", "RESOLVED",
        "Reshoot 2026-04-18", 1, "2026-07-01 09:00:00.000", "",
    ),
    (
        "EXC-0019", "Sc. 63 — prop continuity, wristwatch", "ADVISORY", "WAIVED",
        "Accepted by post supervisor", 1, "2026-07-01 09:00:00.000", "",
    ),
]


# Two further titles, each carrying complete baseline evidence so the outcome
# under test is the synthetic-content rule and not a missing licence.
#
# E07 is the ESCALATE case: an assisted asset with nothing on file for the
# performer. E08 is the HOLD case: consent exists and has been withdrawn. They
# are separate titles so the console's recorded decision stays what it is.
SYNTHETIC_TITLES = ("NORTHSTAR-S01E07", "NORTHSTAR-S01E08")

BASELINE_RECORDED_AT = "2026-07-01 09:00:00.000"
BASELINE_FROM = "2026-06-01 00:00:00.000"
BASELINE_TO = "2027-06-01 00:00:00.000"

# (record_id, title, asset_ref, generation_kind, tool_ref, disclosure_ref)
SYNTHETIC_CONTENT = [
    ("SYN-0006", TITLE, "Episode 6 — full programme", "NONE", "", ""),
    (
        "SYN-0007", SYNTHETIC_TITLES[0], "Sc. 14 — de-aged flashback",
        "ASSISTED", "internal-vfx-2026.3", "NG-DISC-01",
    ),
    (
        "SYN-0008", SYNTHETIC_TITLES[1], "Sc. 22 — synthetic crowd extension",
        "ASSISTED", "internal-vfx-2026.3", "NG-DISC-01",
    ),
]

# (consent_id, title, performer_ref, scope, territory, valid_from, valid_to, status, note)
PERFORMER_CONSENTS = [
    (
        "PC-0031", SYNTHETIC_TITLES[1], "A. Okafor", "likeness", TERRITORY,
        BASELINE_FROM, BASELINE_TO, "ACTIVE", 1, BASELINE_RECORDED_AT, "",
    ),
    (
        "PC-0031", SYNTHETIC_TITLES[1], "A. Okafor", "likeness", TERRITORY,
        BASELINE_FROM, BASELINE_TO, "WITHDRAWN", 2, "2026-08-09 10:15:00.000",
        "Consent withdrawn by performer's representative; recorded 2026-08-09.",
    ),
]


def q(value: str) -> str:
    """Single-quote a SQL string literal."""
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def build_sql() -> str:
    out: list[str] = []
    add = out.append

    add("-- Generated by db/seed.py — do not edit by hand.")
    add("-- Synthetic data. `North Star` is fictional; no fact here describes a")
    add("-- real contractual or regulatory condition in any territory.")
    add("")
    add("INSERT INTO sdl.title_licenses")
    add("(license_id, title_id, territory_code, rights_scope, valid_from, valid_to, status, revision, recorded_at, amendment_note) VALUES")
    rows = [
        f"({q(lid)}, {q(TITLE)}, {q(terr)}, {q(scope)}, {q(vf)}, {q(vt)}, {q(st)}, {rev}, {q(rec)}, {q(note)})"
        for lid, terr, scope, vf, vt, st, rev, rec, note in LICENSES
    ]
    add(",\n".join(rows) + ";")
    add("")

    add("INSERT INTO sdl.clearances")
    add("(clearance_id, title_id, asset_ref, clearance_kind, territory_code, valid_from, valid_to, status, revision, recorded_at, amendment_note) VALUES")
    rows = [
        f"({q(cid)}, {q(TITLE)}, {q(asset)}, {q(kind)}, {q(TERRITORY)}, {q(vf)}, {q(vt)}, {q(st)}, {rev}, {q(rec)}, {q(note)})"
        for cid, asset, kind, vf, vt, st, rev, rec, note in CLEARANCES
    ]
    add(",\n".join(rows) + ";")
    add("")

    add("INSERT INTO sdl.ratings")
    add("(rating_id, title_id, territory_code, rating_code, issued_at, expires_at, status, revision, recorded_at, amendment_note) VALUES")
    rows = [
        f"({q(rid)}, {q(TITLE)}, {q(TERRITORY)}, {q(code)}, {q(iss)}, {q(exp)}, {q(st)}, {rev}, {q(rec)}, {q(note)})"
        for rid, code, iss, exp, st, rev, rec, note in RATINGS
    ]
    add(",\n".join(rows) + ";")
    add("")

    add("INSERT INTO sdl.deliveries")
    add("(delivery_id, title_id, master_version, approved_at, captions_state, audio_description_state, revision, recorded_at, amendment_note) VALUES")
    rows = [
        f"({q(did)}, {q(TITLE)}, {q(mv)}, {q(app)}, {q(cap)}, {q(ad)}, {rev}, {q(rec)}, {q(note)})"
        for did, mv, app, cap, ad, rev, rec, note in DELIVERIES
    ]
    add(",\n".join(rows) + ";")
    add("")

    add("INSERT INTO sdl.continuity_exceptions")
    add("(exception_id, title_id, scene_ref, severity, state, resolution_ref, revision, recorded_at, amendment_note) VALUES")
    rows = [
        f"({q(eid)}, {q(TITLE)}, {q(scene)}, {q(sev)}, {q(state)}, {q(res)}, {rev}, {q(rec)}, {q(note)})"
        for eid, scene, sev, state, res, rev, rec, note in CONTINUITY
    ]
    add(",\n".join(rows) + ";")
    add("")

    payload = canonical_json(POLICY_RULES)
    # Baseline evidence for the synthetic-content titles.
    for extra_title in SYNTHETIC_TITLES:
        suffix = extra_title[-3:]
        add("INSERT INTO sdl.title_licenses")
        add("(license_id, title_id, territory_code, rights_scope, valid_from, valid_to, status, revision, recorded_at, amendment_note) VALUES")
        add(
            f"({q('LIC-NG-0' + suffix)}, {q(extra_title)}, {q(TERRITORY)}, 'SVOD', "
            f"{q(BASELINE_FROM)}, {q(BASELINE_TO)}, 'ACTIVE', 1, {q(BASELINE_RECORDED_AT)}, '');"
        )
        add("")

        add("INSERT INTO sdl.clearances")
        add("(clearance_id, title_id, asset_ref, clearance_kind, territory_code, valid_from, valid_to, status, revision, recorded_at, amendment_note) VALUES")
        rows = [
            f"({q('CLR-' + code + '-' + suffix)}, {q(extra_title)}, {q(asset)}, {q(kind)}, "
            f"{q(TERRITORY)}, {q(BASELINE_FROM)}, {q(BASELINE_TO)}, 'ACTIVE', 1, "
            f"{q(BASELINE_RECORDED_AT)}, '')"
            # Codes are explicit, not derived from the kind: MUSIC_SYNC and
            # MUSIC_MASTER share a prefix, and a shared id collapses the two
            # rows under LIMIT 1 BY into one, silently dropping a mandatory
            # clearance.
            for code, kind, asset in (
                ("MS", "MUSIC_SYNC", "Lagos Nights"),
                ("MM", "MUSIC_MASTER", "Lagos Nights"),
                ("SF", "STOCK_FOOTAGE", "Aerial plate, harbour at dusk"),
                ("TL", "TALENT", f"Ensemble cast — {extra_title}"),
            )
        ]
        add(",\n".join(rows) + ";")
        add("")

        add("INSERT INTO sdl.ratings")
        add("(rating_id, title_id, territory_code, rating_code, issued_at, expires_at, status, revision, recorded_at, amendment_note) VALUES")
        add(
            f"({q('RTG-NG-0' + suffix)}, {q(extra_title)}, {q(TERRITORY)}, '15', "
            f"{q(BASELINE_FROM)}, {q(BASELINE_TO)}, 'VALID', 1, {q(BASELINE_RECORDED_AT)}, '');"
        )
        add("")

        add("INSERT INTO sdl.deliveries")
        add("(delivery_id, title_id, master_version, approved_at, captions_state, audio_description_state, revision, recorded_at, amendment_note) VALUES")
        add(
            f"({q('DLV-0' + suffix)}, {q(extra_title)}, 'v1.0', {q(BASELINE_FROM)}, "
            f"'APPROVED', 'APPROVED', 1, {q(BASELINE_RECORDED_AT)}, '');"
        )
        add("")

    add("INSERT INTO sdl.synthetic_content")
    add("(record_id, title_id, asset_ref, generation_kind, tool_ref, disclosure_obligation_ref, revision, recorded_at, amendment_note) VALUES")
    rows = [
        f"({q(rid)}, {q(t)}, {q(asset)}, {q(kind)}, {q(tool)}, {q(disc)}, 1, "
        f"{q(BASELINE_RECORDED_AT)}, '')"
        for rid, t, asset, kind, tool, disc in SYNTHETIC_CONTENT
    ]
    add(",\n".join(rows) + ";")
    add("")

    add("INSERT INTO sdl.performer_consents")
    add("(consent_id, title_id, performer_ref, consent_scope, territory_code, valid_from, valid_to, status, revision, recorded_at, amendment_note) VALUES")
    rows = [
        f"({q(cid)}, {q(t)}, {q(perf)}, {q(scope)}, {q(terr)}, {q(vf)}, {q(vt)}, "
        f"{q(st)}, {rev}, {q(rec)}, {q(note)})"
        for cid, t, perf, scope, terr, vf, vt, st, rev, rec, note in PERFORMER_CONSENTS
    ]
    add(",\n".join(rows) + ";")
    add("")

    add("INSERT INTO sdl.policy_revisions")
    add("(policy_revision, rules_payload, payload_sha256, effective_at, recorded_at) VALUES")
    add(
        f"({q(POLICY_REVISION)}, {q(payload)}, {q(sha256_of(payload))}, "
        f"{q(POLICY_EFFECTIVE_AT)}, {q(POLICY_RECORDED_AT)});"
    )
    add("")

    payload_v2 = canonical_json(POLICY_RULES_V2)
    add("INSERT INTO sdl.policy_revisions")
    add("(policy_revision, rules_payload, payload_sha256, effective_at, recorded_at) VALUES")
    add(
        f"({q(POLICY_REVISION_V2)}, {q(payload_v2)}, {q(sha256_of(payload_v2))}, "
        f"{q(POLICY_EFFECTIVE_AT_V2)}, {q(POLICY_RECORDED_AT_V2)});"
    )
    add("")

    return "\n".join(out)


def main() -> None:
    target = Path(__file__).parent / "seed.sql"
    target.write_text(build_sql(), encoding="utf-8")
    payload = canonical_json(POLICY_RULES)
    print(f"wrote {target}")
    print(f"policy {POLICY_REVISION} sha256={sha256_of(payload)}")


if __name__ == "__main__":
    main()
