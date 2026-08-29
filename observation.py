"""
Silent Mirror — Observation Engine (v1)

Replaces LLM self-reported signal counts with real cross-session
aggregation. A "pattern" = same (user_id, category, mode) group.
Grouping is by category+mode, not phrase similarity — true semantic
matching is Phase 2a (SentenceTransformer) territory; this is the
deterministic MVP version.

Sources feeding signals:
  - chat: the LLM tags <sig category="..." mode="...">phrase</sig>,
    parsed and logged here.
  - journal: logged automatically on every journal entry — no LLM
    tagging call needed, since mood/mode are already structured fields
    the user provided directly (cheaper, and it's declared data, so it
    outranks inferred chat signals per the declared > inferred rule).
"""

import sqlite3
from datetime import datetime, timedelta

DB_PATH = None  # set by app.py at import time via init(db_path)


def init(db_path: str):
    global DB_PATH
    DB_PATH = db_path


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_signals_table():
    db = get_db()
    db.execute("""CREATE TABLE IF NOT EXISTS signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT,
        category TEXT,      -- recurring_state | mentioned_intent | saved_intent | value_identity
        mode TEXT,
        phrase TEXT,
        source TEXT,        -- chat | journal
        declared INTEGER DEFAULT 0,   -- 1 if user-stated (journal), 0 if inferred (chat)
        status TEXT DEFAULT 'active', -- active | contradicted | retired
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS contradictions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT,
        category TEXT,
        mode TEXT,
        strength TEXT,   -- soft | hard
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    db.commit()
    db.close()


# ---- decay windows, per Part 4A ----
DECAY_DAYS = {
    "recurring_state": 30,     # old mood/energy signals stop counting after 30 days
    "mentioned_intent": None,  # session-based, not day-based — handled separately
    "saved_intent": None,      # never auto-decays; anchored to explicit deadline instead
    "value_identity": 90,      # slow decay — needs a long quiet stretch to fade
}

RETIREMENT_DAYS = {
    "candidate": 45,        # 2 signals, no new signal -> auto-expire
    "eligible": 60,         # 3-4 signals -> downgrade to candidate
    "high_confidence": 90,  # 5+ signals -> downgrade to eligible, never deleted
}


def log_signal(user_id, category, mode, phrase, source="chat"):
    declared = 1 if source == "journal" else 0
    db = get_db()
    db.execute(
        """INSERT INTO signals (user_id, category, mode, phrase, source, declared)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (user_id, category, mode, phrase, source, declared)
    )
    db.commit()
    db.close()


def log_contradiction(user_id, category, mode, strength="soft"):
    """strength: 'soft' or 'hard'. Two soft contradictions in a row
    against the same pattern are escalated to hard by the caller."""
    db = get_db()
    db.execute(
        """INSERT INTO contradictions (user_id, category, mode, strength)
           VALUES (?, ?, ?, ?)""",
        (user_id, category, mode, strength)
    )
    db.commit()
    db.close()


def _active_signal_count(db, user_id, category, mode):
    """Counts signals not yet expired by category-specific decay."""
    decay = DECAY_DAYS.get(category)
    if decay is None:
        rows = db.execute(
            """SELECT COUNT(*) as c FROM signals
               WHERE user_id=? AND category=? AND mode=? AND status='active'""",
            (user_id, category, mode)
        ).fetchone()
    else:
        cutoff = (datetime.utcnow() - timedelta(days=decay)).isoformat()
        rows = db.execute(
            """SELECT COUNT(*) as c FROM signals
               WHERE user_id=? AND category=? AND mode=? AND status='active'
               AND created_at >= ?""",
            (user_id, category, mode, cutoff)
        ).fetchone()
    return rows["c"]


def _tier_from_count(count):
    if count >= 5:
        return "high_confidence"
    if count >= 3:
        return "eligible"
    if count >= 2:
        return "candidate"
    return "none"


def _recent_contradiction_strength(db, user_id, category, mode):
    """Two soft contradictions in a row (no confirming signal between them)
    escalate to hard. Returns 'hard', 'soft', or None."""
    rows = db.execute(
        """SELECT strength FROM contradictions
           WHERE user_id=? AND category=? AND mode=?
           ORDER BY created_at DESC LIMIT 2""",
        (user_id, category, mode)
    ).fetchall()
    if not rows:
        return None
    if rows[0]["strength"] == "hard":
        return "hard"
    if len(rows) == 2 and rows[0]["strength"] == "soft" and rows[1]["strength"] == "soft":
        return "hard"  # recurrence escalation
    return "soft"


def get_evaluation_stats(user_id):
    """
    Real, computed proxy metrics — not hypothetical. Turns the
    affirm/correct rate discussed as an evaluation strategy into an
    actual number, drawn from reflection_feedback. Also returns the
    underlying rows so a user can see exactly which reflections were
    responded to, not just an aggregate count.
    """
    db = get_db()
    rows = db.execute(
        "SELECT response, COUNT(*) as c FROM reflection_feedback WHERE user_id=? GROUP BY response",
        (user_id,)
    ).fetchall()
    history = db.execute(
        """SELECT reflection_text, response, created_at FROM reflection_feedback
           WHERE user_id=? ORDER BY created_at DESC""",
        (user_id,)
    ).fetchall()
    db.close()

    counts = {r["response"]: r["c"] for r in rows}
    affirmed = counts.get("yes", 0)
    corrected = counts.get("no", 0)
    total = affirmed + corrected

    return {
        "total_feedback": total,
        "affirmed": affirmed,
        "corrected": corrected,
        "affirm_rate": round(affirmed / total, 2) if total > 0 else None,
        "history": [dict(r) for r in history],
    }


def get_active_patterns(user_id, min_tier="eligible"):
    """
    Returns computed, ready-to-inject facts for every pattern group at or
    above min_tier — this IS the replacement for the old prose threshold
    tables. The LLM never sees raw signal rows, only this output.
    """
    tier_rank = {"none": 0, "candidate": 1, "eligible": 2, "high_confidence": 3}
    floor = tier_rank[min_tier]

    db = get_db()
    groups = db.execute(
        """SELECT DISTINCT user_id, category, mode FROM signals
           WHERE user_id=? AND status='active'""",
        (user_id,)
    ).fetchall()

    patterns = []
    for g in groups:
        count = _active_signal_count(db, user_id, g["category"], g["mode"])
        tier = _tier_from_count(count)
        if tier_rank[tier] < floor:
            continue

        contradiction = _recent_contradiction_strength(db, user_id, g["category"], g["mode"])
        # weaken effective tier if contradicted, per Part 4 rules
        if contradiction == "hard":
            ranked = max(tier_rank[tier] - 2, 0)
        elif contradiction == "soft":
            ranked = max(tier_rank[tier] - 1, 0)
        else:
            ranked = tier_rank[tier]
        effective_tier = [k for k, v in tier_rank.items() if v == ranked][0]
        if tier_rank[effective_tier] < floor:
            continue

        last = db.execute(
            """SELECT created_at, phrase FROM signals
               WHERE user_id=? AND category=? AND mode=? AND status='active'
               ORDER BY created_at DESC LIMIT 1""",
            (user_id, g["category"], g["mode"])
        ).fetchone()

        patterns.append({
            "category": g["category"],
            "mode": g["mode"],
            "tier": effective_tier,
            "signal_count": count,
            "last_phrase": last["phrase"] if last else "",
            "last_seen": last["created_at"] if last else "",
            "contradiction": contradiction,
        })

    db.close()
    return patterns


def get_saved_insights(user_id):
    """
    User-curated insights from the My Mirror dashboard. These are
    declared data (the user explicitly saved and can edit them) — per
    the declared-beats-inferred rule, they're surfaced to the prompt
    ahead of computed patterns and never subject to decay/retirement,
    since the user is the one deciding when they stop being relevant
    (by editing or deleting), not a timer.
    """
    db = get_db()
    rows = db.execute(
        "SELECT category, mode, user_text FROM saved_insights WHERE user_id=?",
        (user_id,)
    ).fetchall()
    db.close()
    return [dict(r) for r in rows]


def get_recent_journal_entries(user_id, limit=3):
    """
    Actual journal text, not the derived mood signal. Without this, a
    request like 'based on my journal, tell me about myself' had nothing
    real to draw from — SM was answering as if it had journal access it
    didn't actually have.
    """
    db = get_db()
    rows = db.execute(
        "SELECT entry FROM journal WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
        (user_id, limit)
    ).fetchall()
    db.close()
    return [r["entry"] for r in rows]


def get_hobbies(user_id):
    """
    Declared hobby list. Included in context for awareness only — the
    prompt explicitly forbids surfacing these unprompted (see prompts.py).
    Storage here is identical in spirit to saved insights: user-owned,
    never auto-decays.
    """
    db = get_db()
    rows = db.execute("SELECT name FROM hobbies WHERE user_id=?", (user_id,)).fetchall()
    db.close()
    return [r["name"] for r in rows]


def format_context_for_prompt(user_id, min_tier="eligible"):
    """
    Produces the plain-text block that goes into the prompt's
    MIRROR CONTEXT section. This is the ONLY threshold/tier information
    the LLM ever receives — already fully decided, nothing to compute.
    """
    lines = []

    saved = get_saved_insights(user_id)
    for s in saved:
        lines.append(f"- [{s['mode']}] {s['category']} (user-confirmed, no tier — do not use tier-count phrasing for this): {s['user_text']}")

    journal_entries = get_recent_journal_entries(user_id)
    for entry in journal_entries:
        lines.append(f"- (recent journal entry, verbatim, no tier): {entry}")

    hobbies = get_hobbies(user_id)
    if hobbies:
        lines.append(f"- (declared hobbies, awareness only, never bring up unprompted): {', '.join(hobbies)}")

    patterns = get_active_patterns(user_id, min_tier=min_tier)
    for p in patterns:
        line = f"- [{p['mode']}] {p['category']}: tier={p['tier']}, signals={p['signal_count']}, last_seen={p['last_seen']}"
        if p["contradiction"]:
            line += f", contradiction={p['contradiction']}"
        lines.append(line)

    return "\n".join(lines)


def run_retirement_sweep(user_id):
    """
    Call periodically (e.g. once per /chat request, cheap on SQLite at
    this scale). Downgrades stale patterns per the retirement ladder.
    saved_intent signals are exempt — they persist until the user
    resolves them, per the 'stays silent until user brings it up' rule.
    """
    db = get_db()
    groups = db.execute(
        """SELECT DISTINCT category, mode FROM signals
           WHERE user_id=? AND status='active' AND category != 'saved_intent'""",
        (user_id,)
    ).fetchall()

    for g in groups:
        last = db.execute(
            """SELECT created_at FROM signals
               WHERE user_id=? AND category=? AND mode=? AND status='active'
               ORDER BY created_at DESC LIMIT 1""",
            (user_id, g["category"], g["mode"])
        ).fetchone()
        if not last:
            continue

        age_days = (datetime.utcnow() - datetime.fromisoformat(last["created_at"])).days
        count = _active_signal_count(db, user_id, g["category"], g["mode"])
        tier = _tier_from_count(count)

        limit = RETIREMENT_DAYS.get(tier)
        if limit and age_days > limit:
            if tier == "candidate":
                # auto-expire: mark all signals in this group retired
                db.execute(
                    """UPDATE signals SET status='retired'
                       WHERE user_id=? AND category=? AND mode=? AND status='active'""",
                    (user_id, g["category"], g["mode"])
                )
            # eligible/high_confidence "downgrade" happens implicitly —
            # _tier_from_count already reflects only non-expired signals,
            # so once enough individual signals age out of the decay
            # window (for recurring_state/value_identity) the count drops
            # and the tier recalculates lower on its own next call.

    db.commit()
    db.close()