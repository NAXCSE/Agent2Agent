# Agent2Agent

Turn public profile URLs into a judged, ranked set of dating matches.

```
LinkedIn URL + Instagram URL
        ↓
   Apify scraping            services/apify_service.py
        ↓
   Raw LinkedIn + Instagram data
        ↓
   AI analysis                services/profile_analyzer.py     ← any LLM provider
        ↓
   Person Profile             models/schemas.py: PersonProfile
        ↓
   Agent created              services/dating_engine.py
        ↓
   Shortlist (free scoring)   services/matchmaking.py
        ↓
   Agents date each other     services/dating_engine.py
        ↓
   Every conversation judged  services/judge.py
        ↓
   Final ranking              services/ranking_engine.py
```

Gender: **male + female only.** `Gender` is an enum with exactly two values,
same-gender pairs are rejected with an explicit reason, and dating two agents
of the same gender raises.

## Why shortlist before conversing

12 male x 12 female = 144 possible pairs. Conversing and judging all of them is
expensive and slow, so compatibility is first scored for free (deterministic,
no LLM). Only the best `shortlist_per_person` partners per person, capped at
`max_conversations`, are allowed to spend LLM calls. Measured on the 24-person
demo pool: **276 pairs scored for free -> 33 shortlisted -> 8 conversations +
8 judgements.**

Final score blends the free compatibility score with what the judge saw in the
conversation (`prompts/profile_prompt.py: SCORE_WEIGHTS`):

| Signal | Weight | Source |
| --- | --- | --- |
| compatibility | 35% | deterministic profile factors |
| interestingness | 25% | judge on the transcript |
| common ground | 15% | judge, count of shared topics |
| depth | 15% | judge |
| mutual interest | 10% | both sides wanted to continue |

Conversations shorter than `min_messages` are penalised, so length alone cannot
buy a good rank.

## Filters that run before any LLM call

Three gates drop pairs while they are still free, so an obviously impossible
pair never costs a conversation:

| Gate | Behaviour | Where |
| --- | --- | --- |
| gender | male/female only; same-gender pairs are dropped with a reason | `ranking_engine.py` |
| deal breakers | if a stated deal breaker appears in the other person's profile | `ranking_engine.py` |
| age gap | `max_age_gap` years, default 15; ages are read from phrases like "late 60s" or "29s" | `ranking_engine.py` |

Set `max_age_gap` to `null` to disable the age gate, which turns age back into
a 0.10-weighted factor instead of a hard block.

## When nothing matches

An empty result is a normal outcome, not an error: the API still returns 200
with the numbers that explain it.

```json
{
  "total_pairs_considered": 276,
  "pairs_shortlisted": 0,
  "conversations_held": 0,
  "eligible_matches": [],
  "excluded_pairs": [ ... one entry per dropped pair, with reasons ... ],
  "exclusion_summary": { "same_gender": 132, "age_gap": 116 },
  "suggestions": ["116 pair(s) were dropped by the age gate (limit 0 years). ..."]
}
```

`exclusion_summary` counts drops per category (`same_gender`, `deal_breaker`,
`age_gap`, `other`) and `suggestions` says which setting to change. The UI
renders this as a "No viable matches" panel instead of a blank list. Fewer
than two analyzed people is the only hard error (`400`).

## Setup

```cmd
.venv\Scripts\activate
copy backend\.env.example backend\.env
```

| Variable | Needed for |
| --- | --- |
| `APIFY_API_TOKEN` | scraping |
| `LINKEDIN_ACTOR_ID` / `INSTAGRAM_ACTOR_ID` | which Actors to run |
| `LLM_PROVIDER` | `gemini`, `openai`, `openai-compatible`, `anthropic` |
| `GEMINI_API_KEY` / `GEMINI_MODEL` | Gemini provider |
| `GEMINI_FALLBACK_MODELS` | comma-separated fallbacks tried in order |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `OPENAI_MODEL` | OpenAI-compatible provider |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` | Anthropic provider |

`.env` is git-ignored and must never be committed. Tokens are never printed and
are redacted out of error messages.

### Gemini model selection

Free-tier keys lose access to older models. On the account this project was
tested against, `gemini-2.5-flash`, `gemini-2.5-flash-lite` and
`gemini-2.0-flash` all answer `404 no longer available`, and the busiest
models intermittently answer `503 UNAVAILABLE`.

`GeminiClient` handles both without any code change:

- transient `429/500/502/503/504` -> retried on the same model with backoff,
  then rotated to the next candidate;
- `404` / "no longer available" -> rotated immediately;
- when every candidate fails, the error names all of them.

To check what your own key can use:

```python
from google import genai
for model in genai.Client(api_key="...").models.list():
    print(model.name)
```

Working on the free tier at the time of writing: `gemini-3.8-flash` (default),
`gemini-flash-lite-latest`, `gemini-3.5-flash-lite`, `gemini-flash-latest`.

## Running

```cmd
.venv\Scripts\activate
cd backend
uvicorn app.main:app --reload
```

Then open <http://127.0.0.1:8000/>:

1. **Load 24 demo people** (works with no API keys) or paste URLs to analyze.
2. Set the matchmaking knobs (rounds, shortlist size, conversation cap, judge on/off).
3. **Run matchmaking**, then click any ranked pair to read the full
   conversation, its judge scores and the common ground found.

Tests:

```cmd
python test_apify.py        # scraping, one person, prints raw JSON (needs token)
python test_llm_offline.py  # LLM layer with a stub client, no keys needed
python test_pipeline.py     # analysis -> agents -> conversations -> ranking
python test_matchmaking.py  # 12+12 pool, shortlisting, judging, ranking
python test_age_gate.py     # pre-conversation age gate + empty-result handling
```

API:

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api` | service info |
| GET | `/api/health` | provider registry + configured model |
| GET | `/api/demo-people` | 24 pre-analyzed demo profiles |
| POST | `/api/people/analyze` | URLs -> scraped data -> AI person profile |
| POST | `/api/matchmaking` | shortlist -> conversations -> judgement -> ranking |
| POST | `/api/matches` | same, default settings |

If no LLM provider is configured the pipeline still runs: conversations and
judgements fall back to deterministic heuristics and are marked
`generated_by="fallback"` / `judged_by="fallback"` so the UI can show that the
scores are not real judgements.

## Switching LLM providers

Services never import a vendor SDK. They depend on `app.llm.LLMClient`
(`llm/base.py`), and `create_llm()` builds the adapter named by
`LLM_PROVIDER`:

```env
LLM_PROVIDER=gemini
GEMINI_API_KEY=...
```

```env
LLM_PROVIDER=openai-compatible
OPENAI_BASE_URL=http://localhost:11434/v1
OPENAI_MODEL=llama3.1
```

Install the SDK you need (`pip install -r backend/requirements-optional.txt`).
To add a provider: write an adapter in `app/llm/` implementing `generate_json`,
then `register_provider("my-provider", factory)`. No service code changes.

## Layout

```
backend/
  app/
    api/routes.py              FastAPI endpoints
    llm/                       provider-agnostic LLM layer
      base.py                  LLMClient interface + errors
      factory.py               provider registry, create_llm()
      gemini_client.py         google-genai adapter
      openai_client.py         any OpenAI-compatible endpoint
      anthropic_client.py      Claude adapter
      json_utils.py            JSON extraction + schema coercion
    models/schemas.py          Gender, PersonProfile, AgentPerson, MatchResult
    prompts/profile_prompt.py  analysis, persona, conversation and judge prompts
    services/
      apify_service.py         scraping
      profile_analyzer.py      raw data -> AgentPerson
      dating_engine.py         agents + agent-to-agent conversations
      judge.py                 scores a conversation
      matchmaking.py           shortlist -> converse -> judge -> rank
      ranking_engine.py        compatibility scoring, gender filter
      pipeline.py              orchestration
frontend/
  index.html app.js styles.css single-page UI
data/demo_people.json          12 male + 12 female demo profiles
```

## Deploying

```
github  ->  Render (backend API)  ->  Vercel (static frontend)
```

`backend/.env` is never committed. Secrets are set in each host's dashboard.

### 1. GitHub

```cmd
git init
git add -A
git commit -m "Agent2Agent: scraping, agents, judged matchmaking"
git remote add origin https://github.com/<you>/agent2agent.git
git push -u origin main
```

### 2. Backend on Render

`render.yaml` is a Render Blueprint, so the service can be created from the repo
without filling in build/start commands by hand.

- New > Blueprint > point at the repo > pick `render.yaml`
- Then add the secrets in the dashboard (they are deliberately not in git):

| Key | Value |
| --- | --- |
| `APIFY_API_TOKEN` | your Apify token |
| `GEMINI_API_KEY` | your Gemini key |

`CORS_ORIGINS` defaults to `*`. Lock it to your Vercel domain once it exists:

```
CORS_ORIGINS = https://your-app.vercel.app
```

- Health check path is `/api/health`, which answers 200 even with no keys set
  (it reports `llm_ready: false`), so deploys do not fail on missing config
- Free plan cold starts: the first request after idle can take ~30s

### 3. Frontend on Vercel

- New Project > import the repo
- Framework Preset: **Other**
- Root Directory: `frontend`
- Build Command: leave empty
- Output Directory: `frontend`

Then point the UI at the backend by editing one line in `frontend/config.js`:

```js
window.AGENT2AGENT_API = "https://agent2agent-api.onrender.com";
```

Commit that change and Vercel redeploys. You can also test any backend without
editing files: `https://your-app.vercel.app/?api=https://staging.onrender.com`.

Locally the same frontend still works against a locally served API, because an
empty `AGENT2AGENT_API` falls back to the page's own origin.

## Data handling

Raw Apify output is never rewritten or normalized. It is returned as-is and
kept on `PersonProfile.raw`; the flat fields are an added convenience layer.
Long strings are truncated only when building an AI prompt.