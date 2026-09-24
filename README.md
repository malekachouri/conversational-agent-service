# conversational-agent-service

A small customer-support agent built on Claude, set up the way a production AI service needs to be: tools that can be tested on their own, conversation tests that check **outcomes**, Prometheus metrics, a hardened container, and a CI/CD pipeline that scans the image and pushes it with no stored credentials.

The domain is deliberately tiny (look up an order, or hand off to a human). The project is about the engineering around the agent, not the app.

## What it demonstrates

| Area | What's here |
|---|---|
| **Agent + tool calling** | FastAPI `POST /chat` runs a manual Claude tool-use loop (Anthropic Python SDK). The model can call `check_order_status` and `escalate_to_human`. |
| **Tools that stand alone** | Each tool is a plain Python function (`app/tools/functions.py`) plus a JSON schema file (`app/tools/schemas/*.json`). The same schema file is sent to Claude as the tool definition **and** used to validate the model's arguments before the function runs. |
| **Conversation testing** | Multi-turn scenarios written as JSON fixtures are replayed against the API. Assertions cover which tools ran (and which must not), their arguments, the session's end state (escalated or not), and loose reply content. They never compare exact wording. |
| **Deterministic CI** | A rule-based `MockLLM` returns the same content-block shapes as the real API, so the full path (API → agent loop → tools → DB) runs in CI with no API key and no cost. Set `LLM_PROVIDER=anthropic` to run the same fixtures against real Claude. |
| **Persistence** | Conversation history, orders and escalation tickets live in Postgres via SQLAlchemy. Setting `DATABASE_URL` switches to SQLite for local use. |
| **Observability** | `/metrics` exposes request counts, tool-call outcomes by tool and success/failure, and a request-latency histogram. |
| **Supply chain** | Multi-stage, non-root image with pip removed. Replay tests run against the built container. A Trivy scan gates the push, actions are pinned to SHAs, and GHCR gets the push with a short-lived token plus a Sigstore-signed provenance attestation made with the workflow's OIDC token. |

## Architecture

```mermaid
flowchart LR
    client([Client / test harness]) -->|POST /chat<br/>session_id, message| api

    subgraph svc [conversational-agent-service]
        api[FastAPI<br/>app/main.py] --> agent[Agent loop<br/>app/agent.py]
        agent <-->|messages + tool schemas| llm{{LLM backend<br/>app/llm.py}}
        agent -->|validated args| registry[Tool registry<br/>JSON schema validation]
        registry --> t1[check_order_status]
        registry --> t2[escalate_to_human]
        api --> metrics[/GET /metrics/]
    end

    llm -->|LLM_PROVIDER=anthropic| claude[(Claude API)]
    llm -.->|LLM_PROVIDER=mock| mock[MockLLM<br/>deterministic]

    agent -->|conversation history| db[(Postgres / SQLite)]
    t1 -->|orders| db
    t2 -->|escalations| db
    prom[Prometheus] -->|scrape| metrics
```

One `/chat` request goes like this:

```mermaid
sequenceDiagram
    participant C as Client
    participant A as FastAPI + Agent
    participant L as Claude
    participant T as Tools
    participant D as DB
    C->>A: POST /chat {session_id, message}
    A->>D: load session history
    loop until the model stops calling tools (max 6 rounds)
        A->>L: history + new turn, tool schemas
        L-->>A: tool_use blocks (or final text)
        A->>T: validate args against JSON schema, call function
        T->>D: read order / create escalation ticket
        T-->>A: result (fed back as tool_result)
    end
    A->>D: persist the whole turn (one transaction)
    A-->>C: {reply, tool_calls}
```

### Layout

```
app/
  main.py            FastAPI app factory: /chat, /sessions/{id}, /healthz, /metrics
  agent.py           Tool-use loop and system prompt; persists history
  llm.py             AnthropicLLM (real) and MockLLM (deterministic), same interface
  tools/
    functions.py     Plain tool functions: no FastAPI, no LLM
    registry.py      Binds functions to schemas, validates args, isolates failures
    schemas/*.json   Tool definitions: sent to Claude and used for validation
  models.py, db.py   SQLAlchemy models, engine, demo seed data
  metrics.py         Prometheus counters and histogram
fixtures/
  scenario.schema.json         Schema for a conversation scenario
  conversations/*.json         The replayed scenarios
tests/
  harness.py                   Replay + outcome checks
  test_conversations.py        One test per fixture
  test_tools.py                Unit tests for tools/registry
  test_api.py                  API contract + metrics
```

### Claude integration details

- The default model is `claude-opus-5` (change it with `ANTHROPIC_MODEL`). It runs with adaptive thinking and `effort: medium`, a reasonable latency/quality balance for chat (change it with `ANTHROPIC_EFFORT`).
- Each assistant content block, thinking blocks included, is stored and replayed exactly as received, which multi-step tool loops require.
- Tool definitions use `strict: true`, so the model's arguments always match the schema. The registry validates them again anyway, and a failing tool returns an `is_error` tool result instead of crashing the request.
- **Server-side refusal fallback** is on (`fallbacks: "default"`, beta `server-side-fallback-2026-07-01`). If a safety classifier declines a request, the API retries it on Anthropic's recommended fallback model within the same call. If that fallback also refuses, the user gets a polite canned reply.
- Upstream errors come back as HTTP 502/503 (rate limits pass `Retry-After` through). Nothing is written to the database for a failed turn.

## Conversation fixtures

Each file in `fixtures/conversations/` describes one conversation:

```json
{
  "name": "delivered_order_no_escalation",
  "description": "The order was already delivered. The agent should say so and must NOT escalate.",
  "turns": ["Where is my order ORD-1003? I just want to know what's going on with it."],
  "expected": {
    "tools_called": ["check_order_status"],
    "tools_not_called": ["escalate_to_human"],
    "tool_args": {"check_order_status": {"order_id": "ORD-1003"}},
    "final_reply_contains_any": ["delivered"],
    "escalated": false
  }
}
```

| Fixture | Checks |
|---|---|
| `order_status_lookup` | Agent asks for the ID, looks up `ORD-1001`, and says it shipped. No escalation. |
| `unknown_order_escalates` | `ORD-9999` doesn't exist. The agent looks it up, doesn't make up a status, and escalates. |
| `unrelated_question` | An off-topic question. No tool is called. |
| `delivered_order_no_escalation` | `ORD-1003` was delivered. The agent reports that and does **not** escalate. |
| `customer_requests_human` | An explicit request for a human. The agent escalates without looking anything up. |

To add a scenario, drop a new JSON file into the folder. It is validated against `fixtures/scenario.schema.json` and picked up automatically.

Demo orders: `ORD-1001` shipped, `ORD-1002` processing, `ORD-1003` delivered, `ORD-1004` cancelled.

## Running locally

Requires Python 3.11+ (the image uses 3.12).

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env            # add your ANTHROPIC_API_KEY
export $(grep -v '^#' .env | xargs)

uvicorn --factory app.main:create_app --reload     # SQLite at ./dev.db
# no API key? run offline with the mock:
LLM_PROVIDER=mock uvicorn --factory app.main:create_app --reload
```

```bash
curl -s localhost:8000/chat -H 'content-type: application/json' \
  -d '{"session_id": "demo", "message": "Where is my order ORD-1001?"}' | jq
curl -s localhost:8000/sessions/demo | jq
curl -s localhost:8000/metrics | grep -E '^(chat_|agent_)'
```

### Full stack with Docker Compose (API + Postgres + Prometheus)

```bash
cp .env.example .env   # add ANTHROPIC_API_KEY, or run with LLM_PROVIDER=mock
docker compose up --build
# LLM_PROVIDER=mock docker compose up --build
```

- API: http://localhost:8000 (OpenAPI docs at `/docs`)
- Prometheus: http://localhost:9090. For example, try `rate(chat_requests_total[5m])`, `sum by (tool, outcome) (agent_tool_calls_total)` or `histogram_quantile(0.95, rate(chat_request_duration_seconds_bucket[5m]))`

## Running the tests

```bash
# Everything, in-process, with the deterministic mock LLM (what CI runs)
pytest

# Replay the conversation fixtures against real Claude
LLM_PROVIDER=anthropic ANTHROPIC_API_KEY=sk-ant-... pytest -m conversation -v

# Replay against an already running service (docker compose, staging, ...)
AGENT_BASE_URL=http://localhost:8000 pytest tests/test_conversations.py tests/test_api.py -v
```

A failing scenario prints the list of violated expectations and the full transcript:

```
Scenario 'delivered_order_no_escalation' failed:
  - tool 'escalate_to_human' must not be called, but it was
  - expected escalated=False, session has escalated=True

Transcript:
session test-delivered_order_no_escalation-1a2b3c4d
  USER:  Where is my order ORD-1003? ...
  TOOL:  check_order_status({"order_id": "ORD-1003"}) -> {"found": true, "status": "delivered", ...}
  TOOL:  escalate_to_human({"reason": "..."}) -> {...}
  AGENT: ...
```

## Metrics

| Metric | Type | Labels |
|---|---|---|
| `chat_requests_total` | counter | `status` = `success` \| `error` |
| `agent_tool_calls_total` | counter | `tool`, `outcome` = `success` \| `failure` |
| `chat_request_duration_seconds` | histogram | (none). End-to-end `/chat` latency, including every LLM round-trip. |

## CI/CD (`.github/workflows/ci.yml`)

```mermaid
flowchart LR
    push[push / PR] --> test[test<br/>pytest, mock LLM]
    test --> image[image<br/>build → run container →<br/>replay suite over HTTP →<br/>Trivy HIGH/CRITICAL gate]
    image -->|main only| publish[publish<br/>push to GHCR +<br/>signed provenance]
```

1. **test**: installs pinned dependencies and runs the unit tests and conversation suite in-process.
2. **image**: builds the multi-stage image, starts it, replays the conversation suite **against the running container**, then scans it with Trivy. A HIGH or CRITICAL vulnerability that has a fix fails the build.
3. **publish** (pushes to `main` only): loads the *exact image that was scanned*, pushes `ghcr.io/<owner>/conversational-agent-service:{sha-…, latest}`, and attaches a build-provenance attestation.

**No stored credentials.** GHCR doesn't accept OIDC federation directly. The workflow logs in with the job's automatically issued, short-lived `GITHUB_TOKEN`, scoped to `packages: write` in the publish job only. The job's **OIDC token** (`id-token: write`) is used to sign the provenance attestation keylessly through Sigstore. No registry passwords or signing keys live in repository secrets. Verify a pushed image with:

```bash
gh attestation verify oci://ghcr.io/<owner>/conversational-agent-service:latest --owner <owner>
```

## Configuration

| Variable | Default | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | (none) | Required when `LLM_PROVIDER=anthropic` |
| `DATABASE_URL` | `sqlite:///dev.db` | e.g. `postgresql+psycopg://user:pass@host:5432/db` |
| `LLM_PROVIDER` | `anthropic` | `mock` for the deterministic offline backend |
| `ANTHROPIC_MODEL` | `claude-opus-5` | |
| `ANTHROPIC_EFFORT` | `medium` | `low` \| `medium` \| `high` \| `xhigh` \| `max` |
| `MAX_AGENT_ITERATIONS` | `6` | Maximum number of LLM round-trips per user message |
