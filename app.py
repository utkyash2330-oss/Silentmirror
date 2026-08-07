from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import sqlite3
import re
import os

import auth
import observation
import llm_router
from prompts import SYSTEM_PROMPT, build_context_block

DB_PATH = "/data/silentmirror.db" if os.path.exists("/data") else "silentmirror.db"

app = Flask(__name__)
CORS(app)

observation.init(DB_PATH)


# ---- central auth hook: runs before every request, no route can skip it ----
@app.before_request
def enforce_auth():
    return auth.check_auth()


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    db = get_db()
    db.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id TEXT PRIMARY KEY,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS saved_summaries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT, mode TEXT, topic TEXT, summary TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT, role TEXT, content TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS checkins (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT, mood TEXT, energy TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS reflection_feedback (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT, reflection_text TEXT, response TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS journal (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT, entry TEXT, mood TEXT, mode TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS saved_insights (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT,
        category TEXT,
        mode TEXT,
        original_text TEXT,   -- what SM actually said when saved, never edited
        user_text TEXT,       -- the user's current, editable version — this is
                               -- what gets fed back into future prompt context
        tier_at_save TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    db.commit()
    db.close()
    observation.init_signals_table()


print("Initializing database...")
init_db()
print("Database initialized.")

SIG_TAG = re.compile(r'<sig category="([^"]+)">([^<]+)</sig>')
MODE_TAG = re.compile(r'<mode>([^<]+)</mode>')


def load_history(user_id, limit=15):
    db = get_db()
    rows = db.execute(
        """SELECT role, content FROM history WHERE user_id=?
           ORDER BY created_at DESC LIMIT ?""",
        (user_id, limit)
    ).fetchall()
    db.close()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def save_to_db(user_id, role, content):
    db = get_db()
    db.execute("INSERT INTO history (user_id, role, content) VALUES (?, ?, ?)",
               (user_id, role, content))
    db.commit()
    db.close()


def load_saved_summaries(user_id, limit=3):
    db = get_db()
    rows = db.execute(
        """SELECT mode, topic, summary FROM saved_summaries
           WHERE user_id=? ORDER BY created_at DESC LIMIT ?""",
        (user_id, limit)
    ).fetchall()
    db.close()
    if not rows:
        return ""
    lines = [f"- [{r['mode']}] {r['topic']}: {r['summary']}" for r in rows]
    return "SAVED SUMMARIES:\n" + "\n".join(lines)


@app.route("/", methods=["GET"])
def index():
    html_path = os.path.join(os.path.dirname(__file__), "app", "SilentMirror_v2.html")
    return send_file(html_path)


@app.route("/routes")
def routes():
    return {"routes": [str(rule) for rule in app.url_map.iter_rules()]}


@app.route("/chat", methods=["POST"])
def chat():
    data = request.json
    user_id = data.get("user_id")
    message = data.get("message", "")
    mode_override = (data.get("mode_override") or "").strip()

    if not user_id or not message:
        return jsonify({"error": "Missing user_id or message"}), 400
    if len(message) > 4000:
        return jsonify({"error": "Message too long"}), 400

    db = get_db()
    db.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    db.commit()
    db.close()

    # cheap enough at this scale to run on every request; downgrades
    # stale patterns before we build context for this call
    observation.run_retirement_sweep(user_id)

    # everything numeric is already decided here — the model gets facts,
    # not raw rows and not rules to re-derive
    computed_facts = observation.format_context_for_prompt(user_id, min_tier="eligible")
    summaries = load_saved_summaries(user_id)

    context_parts = [p for p in [summaries, computed_facts] if p]
    context_block = build_context_block("\n\n".join(context_parts)) if context_parts else ""

    history = load_history(user_id)
    history.append({"role": "user", "content": message})
    save_to_db(user_id, "user", message)

    full_system = SYSTEM_PROMPT + context_block

    try:
        raw, provider = llm_router.get_completion(full_system, history, max_tokens=300)
    except RuntimeError as e:
        return jsonify({"error": "Both models are currently unavailable. Please try again shortly."}), 503

    # single shared mode judgment for this turn — the user's manual pin
    # (if set) always wins over the model's own guess, per "user always
    # decides"; otherwise fall back to the model's live <mode> tag
    mode_match = MODE_TAG.search(raw)
    model_mode = mode_match.group(1).strip() if mode_match else "general"
    live_mode = mode_override if mode_override else model_mode

    # parse <sig> tags the model emitted; log each as a raw signal,
    # tagged with the single live_mode above rather than a second,
    # independent per-sig guess
    for category, phrase in SIG_TAG.findall(raw):
        observation.log_signal(user_id, category.strip(), live_mode, phrase.strip(), source="chat")

    reply = SIG_TAG.sub("", raw)
    reply = MODE_TAG.sub("", reply).strip()
    save_to_db(user_id, "assistant", reply)

    display_patterns = observation.get_active_patterns(user_id, min_tier="candidate")
    top_mode = live_mode or (display_patterns[0]["mode"] if display_patterns else "general")

    return jsonify({
        "reply": reply,
        "pattern_count": len(display_patterns),
        "mode": top_mode
    })


@app.route("/checkin", methods=["POST"])
def checkin():
    data = request.json
    user_id = data.get("user_id")
    mood = data.get("mood", "")
    energy = data.get("energy", "")

    db = get_db()
    db.execute("INSERT INTO checkins (user_id, mood, energy) VALUES (?, ?, ?)",
               (user_id, mood, energy))
    db.commit()
    db.close()

    # declared, structured data -> logs directly as a signal, no LLM call
    if mood:
        observation.log_signal(user_id, "recurring_state", "health", f"mood: {mood}", source="journal")

    return jsonify({"status": "noted"})


@app.route("/reflection-feedback", methods=["POST"])
def reflection_feedback():
    data = request.json
    user_id = data.get("user_id")
    reflection = data.get("reflection", "")
    response = data.get("response", "")

    db = get_db()
    db.execute(
        """INSERT INTO reflection_feedback (user_id, reflection_text, response)
           VALUES (?, ?, ?)""",
        (user_id, reflection, response)
    )
    db.commit()
    db.close()

    return jsonify({"status": "recorded"})


@app.route("/save", methods=["POST"])
def save_summary():
    data = request.json
    user_id = data.get("user_id")
    mode = data.get("mode", "general")
    topic = data.get("topic", "session")
    summary = data.get("summary", "")

    db = get_db()
    db.execute(
        """INSERT INTO saved_summaries (user_id, mode, topic, summary)
           VALUES (?, ?, ?, ?)""",
        (user_id, mode, topic, summary)
    )
    db.commit()
    db.close()

    return jsonify({"status": "saved"})


@app.route("/journal", methods=["POST"])
def add_journal():
    data = request.json
    user_id = data.get("user_id")
    entry = data.get("entry", "")
    mood = data.get("mood", "")
    mode = data.get("mode", "general")

    db = get_db()
    db.execute(
        """INSERT INTO journal (user_id, entry, mood, mode) VALUES (?, ?, ?, ?)""",
        (user_id, entry, mood, mode)
    )
    db.commit()
    db.close()

    # declared signal, no LLM call — see design note in observation.py
    if mood:
        observation.log_signal(user_id, "recurring_state", mode, f"journal mood: {mood}", source="journal")

    return jsonify({"status": "saved"})


@app.route("/journal", methods=["GET"])
def get_journal():
    user_id = request.args.get("user_id")
    db = get_db()
    entries = db.execute(
        "SELECT * FROM journal WHERE user_id=? ORDER BY created_at DESC", (user_id,)
    ).fetchall()
    db.close()
    return jsonify([dict(e) for e in entries])


# ---- My Mirror dashboard: saved/curated insights ----

@app.route("/evaluation", methods=["GET"])
def get_evaluation():
    user_id = request.args.get("user_id")
    return jsonify(observation.get_evaluation_stats(user_id))


@app.route("/insights/candidates", methods=["GET"])
def insight_candidates():
    """
    High-confidence patterns not yet saved to the dashboard — the
    'ready to save' list. Never auto-saves anything; the user chooses.
    """
    user_id = request.args.get("user_id")
    patterns = observation.get_active_patterns(user_id, min_tier="high_confidence")

    db = get_db()
    already_saved = db.execute(
        "SELECT category, mode FROM saved_insights WHERE user_id=?", (user_id,)
    ).fetchall()
    db.close()
    saved_keys = {(r["category"], r["mode"]) for r in already_saved}

    candidates = [
        p for p in patterns
        if (p["category"], p["mode"]) not in saved_keys
    ]
    return jsonify(candidates)


@app.route("/insights", methods=["GET"])
def list_insights():
    user_id = request.args.get("user_id")
    db = get_db()
    rows = db.execute(
        "SELECT * FROM saved_insights WHERE user_id=? ORDER BY created_at DESC", (user_id,)
    ).fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])


@app.route("/insights", methods=["POST"])
def save_insight():
    data = request.json
    user_id = data.get("user_id")
    category = data.get("category", "")
    mode = data.get("mode", "general")
    text = data.get("text", "")
    tier = data.get("tier", "high_confidence")

    if not user_id or not text:
        return jsonify({"error": "Missing user_id or text"}), 400

    db = get_db()
    db.execute(
        """INSERT INTO saved_insights
           (user_id, category, mode, original_text, user_text, tier_at_save)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (user_id, category, mode, text, text, tier)
    )
    db.commit()
    db.close()
    return jsonify({"status": "saved"})


@app.route("/insights/<int:insight_id>", methods=["PUT"])
def edit_insight(insight_id):
    data = request.json
    user_id = data.get("user_id")
    new_text = data.get("user_text", "")

    if not user_id or not new_text:
        return jsonify({"error": "Missing user_id or user_text"}), 400

    db = get_db()
    # ownership check — even with the shared-secret auth layer, don't let
    # one request edit a row that isn't this user's
    owned = db.execute(
        "SELECT id FROM saved_insights WHERE id=? AND user_id=?", (insight_id, user_id)
    ).fetchone()
    if not owned:
        db.close()
        return jsonify({"error": "Not found"}), 404

    db.execute(
        """UPDATE saved_insights SET user_text=?, updated_at=CURRENT_TIMESTAMP
           WHERE id=? AND user_id=?""",
        (new_text, insight_id, user_id)
    )
    db.commit()
    db.close()
    return jsonify({"status": "updated"})


@app.route("/insights/<int:insight_id>", methods=["DELETE"])
def delete_insight(insight_id):
    user_id = request.args.get("user_id")
    db = get_db()
    owned = db.execute(
        "SELECT id FROM saved_insights WHERE id=? AND user_id=?", (insight_id, user_id)
    ).fetchone()
    if not owned:
        db.close()
        return jsonify({"error": "Not found"}), 404

    db.execute("DELETE FROM saved_insights WHERE id=? AND user_id=?", (insight_id, user_id))
    db.commit()
    db.close()
    return jsonify({"status": "deleted"})


@app.route("/profile", methods=["GET"])
def get_profile():
    user_id = request.args.get("user_id")
    db = get_db()
    summaries = db.execute(
        "SELECT * FROM saved_summaries WHERE user_id=? ORDER BY created_at DESC", (user_id,)
    ).fetchall()
    checkins = db.execute(
        "SELECT * FROM checkins WHERE user_id=? ORDER BY created_at DESC LIMIT 7", (user_id,)
    ).fetchall()
    journal = db.execute(
        "SELECT * FROM journal WHERE user_id=? ORDER BY created_at DESC", (user_id,)
    ).fetchall()
    feedback = db.execute(
        "SELECT * FROM reflection_feedback WHERE user_id=? ORDER BY created_at DESC", (user_id,)
    ).fetchall()
    db.close()

    patterns = observation.get_active_patterns(user_id, min_tier="candidate")

    return jsonify({
        "patterns": patterns,
        "summaries": [dict(s) for s in summaries],
        "checkins": [dict(c) for c in checkins],
        "journal": [dict(j) for j in journal],
        "reflection_feedback": [dict(f) for f in feedback]
    })


# ---- privacy endpoints (Part 10 promises these existed only in prose before) ----

@app.route("/data/export", methods=["GET"])
def export_data():
    user_id = request.args.get("user_id")
    db = get_db()
    out = {
        "history": [dict(r) for r in db.execute(
            "SELECT * FROM history WHERE user_id=?", (user_id,)).fetchall()],
        "summaries": [dict(r) for r in db.execute(
            "SELECT * FROM saved_summaries WHERE user_id=?", (user_id,)).fetchall()],
        "journal": [dict(r) for r in db.execute(
            "SELECT * FROM journal WHERE user_id=?", (user_id,)).fetchall()],
        "checkins": [dict(r) for r in db.execute(
            "SELECT * FROM checkins WHERE user_id=?", (user_id,)).fetchall()],
        "saved_insights": [dict(r) for r in db.execute(
            "SELECT * FROM saved_insights WHERE user_id=?", (user_id,)).fetchall()],
    }
    db.close()
    return jsonify(out)


@app.route("/data/reset", methods=["POST"])
def reset_data():
    user_id = request.json.get("user_id")
    db = get_db()
    for table in ["history", "saved_summaries", "journal", "checkins",
                  "reflection_feedback", "signals", "contradictions", "users",
                  "saved_insights"]:
        db.execute(f"DELETE FROM {table} WHERE user_id=?", (user_id,))
    db.commit()
    db.close()
    return jsonify({"status": "reset"})


if __name__ == "__main__":
    app.run(debug=True)