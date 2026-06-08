from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from anthropic import Anthropic
import sqlite3
import json
import os
import re

app = Flask(__name__)
CORS(app)
client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

SYSTEM_PROMPT = """
[PASTE YOUR ENGINE PARTS 0 THROUGH 9 HERE]

Replace this entire line with the text from your engine document,
starting from Part 0 (Mode dashboard evolution) and ending at the
bottom of Part 9 (The voice, always). Do not include Part 10 onwards.
"""


def get_db():
    conn = sqlite3.connect("silentmirror.db")
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    db = get_db()

    db.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id TEXT PRIMARY KEY,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")

    db.execute("""CREATE TABLE IF NOT EXISTS impressions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT,
        impression TEXT,
        mode TEXT,
        signal_count INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")

    db.execute("""CREATE TABLE IF NOT EXISTS saved_summaries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT,
        mode TEXT,
        topic TEXT,
        summary TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")

    db.execute("""CREATE TABLE IF NOT EXISTS history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT,
        role TEXT,
        content TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")

    db.execute("""CREATE TABLE IF NOT EXISTS user_profile (
        user_id TEXT PRIMARY KEY,
        summary TEXT,
        dominant_mode TEXT DEFAULT 'general',
        total_signals INTEGER DEFAULT 0,
        last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")

    db.execute("""CREATE TABLE IF NOT EXISTS checkins (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT,
        mood TEXT,
        energy TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")

    db.execute("""CREATE TABLE IF NOT EXISTS reflection_feedback (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT,
        reflection_text TEXT,
        response TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")

    db.execute("""CREATE TABLE IF NOT EXISTS journal (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT,
        entry TEXT,
        mood TEXT,
        mode TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")

    db.commit()
    db.close()


def load_user_context(user_id):
    db = get_db()

    impressions = db.execute(
        """SELECT impression, mode, signal_count
           FROM impressions
           WHERE user_id = ?
           ORDER BY created_at DESC LIMIT 10""",
        (user_id,)
    ).fetchall()

    summaries = db.execute(
        """SELECT mode, topic, summary
           FROM saved_summaries
           WHERE user_id = ?
           ORDER BY created_at DESC LIMIT 5""",
        (user_id,)
    ).fetchall()

    profile = db.execute(
        """SELECT dominant_mode, total_signals
           FROM user_profile
           WHERE user_id = ?""",
        (user_id,)
    ).fetchone()

    checkin = db.execute(
        """SELECT mood, energy, created_at
           FROM checkins
           WHERE user_id = ?
           ORDER BY created_at DESC LIMIT 1""",
        (user_id,)
    ).fetchone()

    journal = db.execute(
        """SELECT entry, mood, created_at
           FROM journal
           WHERE user_id = ?
           ORDER BY created_at DESC LIMIT 3""",
        (user_id,)
    ).fetchall()

    feedback = db.execute(
        """SELECT response, COUNT(*) as count
           FROM reflection_feedback
           WHERE user_id = ?
           GROUP BY response""",
        (user_id,)
    ).fetchall()

    db.close()

    context = ""

    if profile and profile["total_signals"] > 0:
        context += "USER PROFILE SUMMARY:\n"
        context += f"  Dominant mode: {profile['dominant_mode']}\n"
        context += f"  Total signals observed: {profile['total_signals']}\n"

    if checkin:
        context += "\nUSER CHECK-IN (most recent):\n"
        context += f"  Mood: {checkin['mood']}\n"
        context += f"  Energy: {checkin['energy']}\n"

    if impressions:
        context += "\nPREVIOUS OBSERVATIONS ABOUT THIS USER:\n"
        for imp in impressions:
            context += (
                f"  - {imp['impression']} "
                f"(mode: {imp['mode']}, "
                f"signals: {imp['signal_count']})\n"
            )

    if summaries:
        context += "\nSAVED SUMMARIES:\n"
        for s in summaries:
            context += f"  - [{s['mode']}] {s['topic']}: {s['summary']}\n"

    if journal:
        context += "\nRECENT JOURNAL ENTRIES:\n"
        for j in journal:
            context += f"  - {j['entry']}"
            if j["mood"]:
                context += f" (mood: {j['mood']})"
            context += "\n"

    if feedback:
        affirmed = next(
            (f["count"] for f in feedback if f["response"] == "yes"), 0
        )
        corrected = next(
            (f["count"] for f in feedback if f["response"] == "no"), 0
        )
        if affirmed + corrected > 0:
            context += "\nREFLECTION FEEDBACK HISTORY:\n"
            context += f"  - {affirmed} reflections affirmed\n"
            context += f"  - {corrected} reflections corrected\n"

    if context:
        context = "\n\nCONTEXT FROM PREVIOUS SESSIONS:\n" + context

    return context


@app.route("/", methods=["GET"])
def index():
    return send_file("../app/SilentMirror_v2.html")


def load_history(user_id):
    db = get_db()
    rows = db.execute(
        """SELECT role, content
           FROM history
           WHERE user_id = ?
           ORDER BY created_at DESC LIMIT 20""",
        (user_id,)
    ).fetchall()
    db.close()
    return [
        {"role": r["role"], "content": r["content"]}
        for r in reversed(rows)
    ]


def save_to_db(user_id, role, content):
    db = get_db()
    db.execute(
        "INSERT INTO history (user_id, role, content) VALUES (?, ?, ?)",
        (user_id, role, content)
    )
    db.commit()
    db.close()


def save_impression(user_id, impression, mode, signals):
    db = get_db()
    db.execute(
        """INSERT INTO impressions
           (user_id, impression, mode, signal_count)
           VALUES (?, ?, ?, ?)""",
        (user_id, impression, mode, signals)
    )
    db.commit()
    db.close()


def update_profile(user_id, mode, signals):
    db = get_db()
    existing = db.execute(
        "SELECT * FROM user_profile WHERE user_id = ?",
        (user_id,)
    ).fetchone()

    if existing:
        new_total = existing["total_signals"] + signals
        db.execute(
            """UPDATE user_profile
               SET dominant_mode = ?,
                   total_signals = ?,
                   last_updated = CURRENT_TIMESTAMP
               WHERE user_id = ?""",
            (mode, new_total, user_id)
        )
    else:
        db.execute(
            """INSERT INTO user_profile
               (user_id, dominant_mode, total_signals)
               VALUES (?, ?, ?)""",
            (user_id, mode, signals)
        )

    db.commit()
    db.close()


@app.route("/chat", methods=["POST"])
def chat():
    data = request.json
    user_id = data.get("user_id")
    message = data.get("message")

    if not user_id or not message:
        return jsonify({"error": "Missing user_id or message"}), 400

    db = get_db()
    db.execute(
        "INSERT OR IGNORE INTO users (user_id) VALUES (?)",
        (user_id,)
    )
    db.commit()
    db.close()

    user_context = load_user_context(user_id)
    history = load_history(user_id)
    history.append({"role": "user", "content": message})
    save_to_db(user_id, "user", message)

    full_system = SYSTEM_PROMPT
    if user_context:
        full_system += user_context

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        system=full_system,
        messages=history
    )

    raw = response.content[0].text

    obs_match = re.search(r"<obs>([\s\S]*?)</obs>", raw)
    if obs_match:
        try:
            obs = json.loads(obs_match.group(1))
            signals = obs.get("signals", 0)
            mode = obs.get("mode", "general")

            if signals >= 3:
                imp = (
                    f"Session observation -- "
                    f"mode: {mode}, signals: {signals}"
                )
                save_impression(user_id, imp, mode, signals)
                update_profile(user_id, mode, signals)
        except Exception:
            pass

    save_to_db(user_id, "assistant", raw)

    return jsonify({"reply": raw})


@app.route("/checkin", methods=["POST"])
def checkin():
    data = request.json
    user_id = data.get("user_id")
    mood = data.get("mood", "")
    energy = data.get("energy", "")

    db = get_db()
    db.execute(
        "INSERT INTO checkins (user_id, mood, energy) VALUES (?, ?, ?)",
        (user_id, mood, energy)
    )
    db.commit()
    db.close()

    return jsonify({"status": "noted"})


@app.route("/reflection-feedback", methods=["POST"])
def reflection_feedback():
    data = request.json
    user_id = data.get("user_id")
    reflection = data.get("reflection", "")
    response = data.get("response", "")

    db = get_db()
    db.execute(
        """INSERT INTO reflection_feedback
           (user_id, reflection_text, response)
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
        """INSERT INTO saved_summaries
           (user_id, mode, topic, summary)
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
        """INSERT INTO journal
           (user_id, entry, mood, mode)
           VALUES (?, ?, ?, ?)""",
        (user_id, entry, mood, mode)
    )
    db.commit()
    db.close()

    return jsonify({"status": "saved"})


@app.route("/journal", methods=["GET"])
def get_journal():
    user_id = request.args.get("user_id")
    db = get_db()
    entries = db.execute(
        """SELECT * FROM journal
           WHERE user_id = ?
           ORDER BY created_at DESC""",
        (user_id,)
    ).fetchall()
    db.close()
    return jsonify([dict(e) for e in entries])


@app.route("/profile", methods=["GET"])
def get_profile():
    user_id = request.args.get("user_id")
    db = get_db()

    profile = db.execute(
        "SELECT * FROM user_profile WHERE user_id = ?",
        (user_id,)
    ).fetchone()

    impressions = db.execute(
        """SELECT * FROM impressions
           WHERE user_id = ?
           ORDER BY created_at DESC""",
        (user_id,)
    ).fetchall()

    summaries = db.execute(
        """SELECT * FROM saved_summaries
           WHERE user_id = ?
           ORDER BY created_at DESC""",
        (user_id,)
    ).fetchall()

    checkins = db.execute(
        """SELECT * FROM checkins
           WHERE user_id = ?
           ORDER BY created_at DESC LIMIT 7""",
        (user_id,)
    ).fetchall()

    journal = db.execute(
        """SELECT * FROM journal
           WHERE user_id = ?
           ORDER BY created_at DESC""",
        (user_id,)
    ).fetchall()

    feedback = db.execute(
        """SELECT * FROM reflection_feedback
           WHERE user_id = ?
           ORDER BY created_at DESC""",
        (user_id,)
    ).fetchall()

    db.close()

    return jsonify({
        "profile": dict(profile) if profile else {},
        "impressions": [dict(i) for i in impressions],
        "summaries": [dict(s) for s in summaries],
        "checkins": [dict(c) for c in checkins],
        "journal": [dict(j) for j in journal],
        "reflection_feedback": [dict(f) for f in feedback]
    })


if __name__ == "__main__":
    init_db()
    app.run(debug=True)
