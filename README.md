# Silent Mirror

We make countless micro-decisions every day. Some seem insignificant, yet they can shape relationships, opportunities, and personal growth. These decisions are often influenced by the version of ourselves leading in that moment — calm, emotional, stressed, confident, or uncertain. In busy or vulnerable states, it isn't always clear whether we're thinking rationally, emotionally, or simply reacting to pressure. Looking back, greater self-awareness might have led us to make different choices.

What if there were a technical partner that grew alongside us — not to decide for us, but to help us understand ourselves better over time?

That is the idea behind Silent Mirror.

---

## What It Actually Is

Silent Mirror is a memory-augmented conversational AI system: an LLM handles generation and light observation-tagging, while a fully deterministic Python layer owns all counting, decay, and confidence scoring. The two are deliberately separated — the model never calculates a confidence tier, and the code never generates language. It helps with whatever the user needs (study, health, general conversation) while quietly building a longitudinal picture of behavioral patterns over time, and reflecting that picture back only when there's real evidence behind it — never as unsolicited advice.

**Core principle:** *Machines assist. Humans decide.*

---

## Key Features

- **Conversational core** — general-purpose chat with silent, per-turn mode detection (study/health/mirror/custom/general), with a manual override if the model's guess is wrong
- **Pattern engine** — signals are categorized (`recurring_state`, `mentioned_intent`, `saved_intent`, `value_identity`) and only surfaced as a reflection once they cross a real, counted confidence threshold — never fabricated
- **My Mirror dashboard** — view patterns that reached high confidence, save and *edit* them (not just delete — identity is treated as something that updates, not a fixed label), and see real computed evaluation metrics
- **Journal** — free-form entries, genuinely retrievable later, feeding real context (not just a derived mood tag)
- **Consent-first design** — every reflection is tentative ("I may be wrong... does this feel accurate?"), nothing is diagnosed, nothing is prescribed unless directly asked
- **Multi-provider resilience** — automatic fallback between LLM providers on rate limits, without masking real errors
- **Full data control** — export, reset, or delete any stored insight at any time

---

## Architecture

```
Problem → Design Goals → Components → Memory → Pattern Engine → Confidence → Evaluation → Limitations → Roadmap
```

**Components:** Flask backend on Railway, SQLite on a persistent volume, a deterministic observation engine (`observation.py`), an LLM router with automatic provider fallback, and a single-page vanilla JS frontend.

**Memory (three layers, different guarantees):**
1. Raw conversation history — last 15 messages, pure recency
2. Signals — individually tagged, categorized, timestamped observations
3. Saved insights & journal entries — user-curated, declared, never auto-decay

**Confidence** is tier-based (`candidate` → `eligible` → `high_confidence`), not a fabricated probability score — a deliberate choice made after explicitly rejecting a fake-percentage design, since a made-up "75% confident" would be less honest than an accurate "this has come up 4 times."

**Evaluation** has no ground truth to check against — no external record proves a reflection is "true." Real proxy metrics are computed instead: affirm/correct rate from direct user feedback, visible in-app under My Mirror → Evaluation.

---

## Real Example — From Actual Testing

The following is an unedited excerpt from a real testing session, included because it's more honest evidence of the system's behavior than a curated demo would be:

> **You:** "Kehte hain kuch paane ke liye kuch khona padta hai, Apnapan bachane ko agar khud se haar gaye, toh kya gila, kya shikwa."
>
> **Silent Mirror:** It seems like you shared a poetic phrase in Hindi. The translation roughly means: "They say that to gain something, you have to lose something. If you lose to yourself to save a relationship, then what's the complaint, what's the grievance?" It's a thought-provoking and introspective quote. Would you like to talk about what inspired you to share this, or how it relates to your current thoughts and feelings?

A session later, testing real cross-session recall:

> **You:** "what we were talking in last session"
>
> **Silent Mirror:** We were discussing your journey of personal growth, self-reflection, and exploration. You shared a poetic quote in Hindi that resonated with you, and we delved into its meaning and significance in the context of your life... [Accurate] [Not quite]

That last exchange is a genuine confidence-feedback moment — the `[Accurate] / [Not quite]` buttons write directly to the evaluation metrics shown in the dashboard, not a decorative UI element.

---

## Known Limitations

Silent Mirror has been tested against its own failure modes deliberately, not just built and left untested. Some are fixed, some are inherent to the category of system:

- **Cold-start problem** — every new pattern starts with zero evidence; no shortcut around this
- **No ground truth for evaluation** — reflections are evaluated by proxy (user confirmation), which itself carries a risk of false-positive agreement (the Barnum effect) — a structural limitation, not a bug
- **Observer effect** — being reflected back at can change the behavior being observed; mitigated by tentative, non-prescriptive language, not fully solvable
- **Miscategorization risk** — a wrong category/mode label silently affects decay and threshold behavior; harder to detect than an outright wrong statement
- **Recency-based retrieval only** — no semantic/relevance-based retrieval yet (planned, see Roadmap); saved insights currently scope to "always included" rather than "included when relevant"

---

## Roadmap

- **Phase 2a** — embedding-based semantic retrieval, replacing pure recency with relevance
- **Phase 2b** — a small trained confidence classifier (PyTorch), layered on top of the existing rule-based tiers, not replacing them
- Deeper mode-picker integration into retrieval scoping
- Prompt migrated from local fallback to a Railway environment variable once design has stabilized further

---

*Machines assist. Humans decide. Silent Mirror is not an AI you follow.*