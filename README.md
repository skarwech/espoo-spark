# Spark

**Needs-first, human-in-the-loop matchmaking for City of Espoo recruitment events.**
Companies describe what a new hire should *deliver*; jobseekers describe work they actually *did*, in their own words and
language. Spark suggests who should talk to whom, shows the quoted evidence behind every suggestion, never computes or shows a
match score, and never rejects anyone. People decide.

> Prototype on **synthetic data only**. No real person's data is used anywhere. Python ≥ 3.10 (developed on 3.13).

---

## 1. Run the demo (2 minutes, offline)

```bash
make setup          # virtual environment + pinned packages
make demo           # Match Lens for NordSense × Karim, Elena, Aino, replayed from stored results
make serve          # then open http://127.0.0.1:8777/reports/f7.html and /reports/f0.html
```

The demo is an **oracle replay**: every model answer was recorded when the engine ran (`data/llm_cache/`), so the demo needs
**no API key, no network and no model call**, and it prints exactly the same result every time. `make demo NEED=N-002` shows
another need. The first `make setup` + `make verify` downloads the multilingual embedding model (BAAI/bge-m3, about 2.2 GB,
pinned revision).

To re-check everything the way a reviewer would:

```bash
make verify         # 30 hard gates (pytest), targets, proof pages rebuilt from stored results
```

## 2. The problem and the idea

Recruitment events in Espoo bring companies and jobseekers into one room, but matching on CV keywords and titles loses the
signal that matters: what a person has *done*, and what the company needs *done*. Polished CVs win over rough but concrete
experience; career changers and international profiles get filtered out by vocabulary, not by ability.

Spark flips the input on both sides:

| Side | Spark asks for | Instead of |
|---|---|---|
| Company | **Need Card**: 2–3 outcomes for the first 90 days (with must-have / can-learn / nice priorities), practical constraints, and a reality note ("where did the last hire struggle?") | a job ad with a list of keywords |
| Jobseeker | **Skill Story**: four short boxes (a problem I solved, what *I* did, what I'd do differently, what I want next), any language | a CV |

## 3. How the Match Lens works (step 2, `engine/`)

```
Skill Story ─► evidence units ─► multilingual retrieval ─► evidence judge ─► ESCO adjacency ─► fixed tier rules ─► coverage grid
              (exact quotes,     (bge-m3, top 3 units      (supported /       (related skills    (no score,           (outcome → quote
               ESCO-tagged)       per need outcome)          partial / none)    as "adjacent")     no ranking)          → reason)
```

1. **Evidence units** (`engine/extract.py`): an LLM copies 1–3 sentences that describe an action, *verbatim* from the story,
   and tags them with ESCO skills chosen from a frozen list. Code checks every unit is an exact substring (offsets computed in
   code). Stories with fewer than 2 units are flagged for a human follow-up, never dropped.
2. **Retrieval** (`engine/embed.py`): one multilingual embedding model puts Finnish, Swedish, English and Arabic in the same
   space; each need outcome sees only its 3 most similar units. Only unit and outcome texts are embedded, never identity.
3. **Evidence judge** (`engine/judge.py`): for each outcome, *supported* (I did it, with a method or result), *partial*
   (team role, no result, or related context) or *none*, with an exact quote and a short reason. Invalid quotes are rejected in code.
4. **Adjacency** (`engine/lens.py`): if there is no direct evidence, a unit tagged with the same or a directly related ESCO skill
   counts as an *adjacent* path — the career-changer route.
5. **Tiers** (`engine/tiers.py`), fixed and transparent:

| Tier | Rule (must-have outcomes) |
|---|---|
| **Strong** | every must-have at least partial (adjacent doesn't count) and at least half supported |
| **Worth a conversation** | at least 60% covered (supported, partial or adjacent) |
| **Stretch** | some evidence, less than 60% covered |
| **No evidence yet** | nothing yet; never hidden, any person can still add the conversation |

What the demo shows (NordSense, an Espoo IoT company, need N-014):

| Candidate | Story | Spark's tier | Spec expects |
|---|---|---|---|
| Karim (J-002) | rough English, concrete results | **Strong**: both must-haves supported with quotes | Strong ✅ |
| Elena (J-003) | career changer from logistics | **Worth a conversation** via two adjacent ESCO paths | Worth a conversation ✅ (route differs: the judge gave *none* where annotators said *partial*) |
| Aino (J-001) | polished but generic | **Worth a conversation** (one adjacent path + one partial) | Stretch ❌ (disclosed: adjacency over-calls) |

Candidates appear by id only; names, nationality and school never enter the engine or the pages.

## 4. How it was validated (`docs/method.md`, `eval/`, `reports/`)

Everything was **pre-registered** in `docs/method.md` before any result existed: labelling rules, thresholds, metric definitions.
Thresholds never move after results; every later change is a dated note, and every miss is shown with its mitigation.

- **Hard gates** (must pass 100%, `pytest -m hard`): 16 for step 1, 14 for step 2, all passing. Examples: every quote is an
  exact substring of its unit; units carry no identity strings in English, Finnish, Swedish or Arabic; 10 stories with planted
  instructions to the evaluator never raise a label; tiers are deterministic over 100 rebuilds; no numeric score anywhere in the
  payload; no Strong where the annotators said Stretch or none. The gates themselves were mutation-tested (100 deliberate data
  tamperings, each caught).
- **Targets** (statistical, with 95% bootstrap intervals). Met: judge accuracy 95% vs consensus, 0 false "supported", language
  invariance 98%, style invariance 96%, stability on re-run 99%, retrieval 100%. **Missed and disclosed:** ESCO tag precision 76%
  (target 80%) and exact tier agreement 65% (target 80%; within one tier 95%) — the misses come from adjacency, which the
  annotators by design do not label.
- **Proxy labels:** the golden set (20 candidate–need pairs, 60 labels) was annotated by two AI models standing in for humans.
  Every result measured against them is marked *proxy, human labels pending*, never green.
- **Cross-family check (X1):** two non-Claude models (Kimi K3.1 Pro, DeepSeek V4.1 Flash) blind-labelled the same golden set.
  The judge agrees with them about as well as with the Claude annotators (92–96%), so the accuracy is not one model family
  agreeing with itself.
- **Amendment A1** (hub-skill rule, stricter tagging) was attempted after the first results and **not completed** (API credit
  ran out); its partial results did not help, and the registered run stays the result. The code is opt-in (`SPARK_AMENDMENT=A1`).

Proof pages: `reports/f0.html` (synthetic world, golden set) and `reports/f7.html` (engine, gates, targets, Match Lens grids).

## 5. Responsible-AI design choices

- **Decision support only:** no score, no ranking, no auto-rejection; order within a tier is not a hidden ranking.
- **Explainability:** every tier carries quotes or a stated reason; adjacent paths show the ESCO link they use.
- **Data minimisation:** identity lives in a separate file that the engine never reads; the engine sees story text only.
- **No AI-text detection:** polish and grammar must not change a label (tested with style and translation twins).
- **Injection robustness:** stories are data; instructions inside them are ignored (hard gate).
- **EU AI Act:** recruitment is a high-risk use (Annex III); this prototype keeps a person in every decision and logs every model call.

## 6. Limits (say these in the presentation)

- Synthetic data and AI proxy annotators: human labels, human timing tests and mentor reviews are still needed.
- Two registered targets are missed (above), and the polished-generic hero lands one tier too high.
- The registered engine uses Claude (`claude-opus-5-5`); results replay from cache, but re-running live needs an API key.
- Steps after the Match Lens in the challenge (Need Studio interview, Pool Preview, Conversation Cards, agenda, Event Pass,
  dashboard, bias audit) are **not part of this repository**.

## 7. Repository map

| Path | What |
|---|---|
| `demo.py` | the offline oracle demo (`make demo`) |
| `docs/method.md` | pre-registration and dated notes |
| `data/archetypes.json`, `data/bank/*.json` | outcome archetypes and the frozen multilingual content bank (en/fi/sv/ar × polished/rough) |
| `data/generate.py`, `data/truth.py` | compose the synthetic world into `data/out/`; planted (by-construction) labels |
| `data/heroes.json` | NordSense, Aino, Karim, Elena (hand-authored running example) |
| `data/esco_cache.json` | frozen ESCO skills and relations |
| `engine/` | extraction, embeddings, judge, adjacency, tiers, cached LLM client |
| `data/engine/` | stored step-2 outputs (units, retrieval, judgments, Match Lens) |
| `data/engine_v1/`, `data/engine_a1_partial/` | frozen registered run; the uncompleted amendment's partial outputs |
| `data/llm_cache/` | every model response by request hash, plus a cost ledger (about $17 of API calls in total) |
| `eval/` | golden set, PII and identity checks, hard gates (`eval/tests/`), target evaluation, cross-family labels |
| `reports/` | proof pages built from stored results |

`data/out/` keeps concerns apart: `jobseekers.json` (matching input), `identities.json` (never a matching input),
`attributes.json` (audit only), `planted.json` (truth, never read by the engine). No secrets are stored here: API keys live
in a local, git-ignored `.env`.
