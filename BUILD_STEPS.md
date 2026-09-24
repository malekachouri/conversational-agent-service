# How This Project Was Built, Step by Step

This file lists, in order, every step taken to build `conversational-agent-service` from an empty folder, with the commands used and the reason behind each decision. The code itself is in the repository. Here each step points to the files it produced and shows the most important parts.

> New to the concepts (agent, tool, Docker, CI/CD…)? Read [GUIDE.md](GUIDE.md) first.

## Overview

| # | Step | Files produced |
|---|---|---|
| 0 | Define the requirements | – |
| 1 | Check tool versions and choose the stack | – |
| 2 | Create the folder structure | folders |
| 3 | Configuration | `app/config.py` |
| 4 | Database tables and demo data | `app/models.py`, `app/db.py` |
| 5 | The tools (functions + JSON schemas) | `app/tools/` |
| 6 | The LLM layer (real Claude + mock) | `app/llm.py` |
| 7 | The agent loop | `app/agent.py` |
| 8 | Metrics | `app/metrics.py` |
| 9 | The web API | `app/main.py` |
| 10 | Conversation fixtures | `fixtures/` |
| 11 | Test harness and tests | `tests/` |
| 12 | Dependencies and project files | `requirements*.txt`, `pyproject.toml`, `.env.example`, `.gitignore` |
| 13 | First test run | – |
| 14 | Checking the real Claude request without spending money | – |
| 15 | Dockerfile | `Dockerfile`, `.dockerignore` |
| 16 | Docker Compose and Prometheus | `docker-compose.yml`, `prometheus/prometheus.yml`, `Makefile` |
| 17 | Testing the container (SQLite, then Postgres) | – |
| 18 | Security scan with Trivy | – |
| 19 | CI/CD workflow | `.github/workflows/ci.yml` |
| 20 | Documentation | `README.md`, `GUIDE.md`, `BUILD_STEPS.md` |
| 21 | Git commit and packaging as a zip | – |

---

## Step 0: Define the requirements

Before writing any code, the goal was written down:

- **Agent:** a FastAPI `POST /chat` endpoint takes `(session_id, message)` and returns `(reply, tool_calls)`. It calls Claude with tool calling enabled.
- **Two tools:** `check_order_status(order_id)` and `escalate_to_human(reason)`. Each is a plain Python function with a JSON schema, and the schema is also what describes the tool to the LLM.
- **Storage:** conversation history and an `orders` table in Postgres through SQLAlchemy, with SQLite as the fallback (`DATABASE_URL`, default `sqlite:///dev.db`).
- **Testing (the priority):** replay multi-turn conversations from JSON fixture files and check **outcomes** (tools called or not called, final state, reply content), never exact wording. At least 4 scenarios.
- **Observability:** `/metrics` with total requests, tool-call outcomes by tool and success/failure, and a latency histogram.
- **Delivery:** a multi-stage non-root Dockerfile, docker-compose (API + Postgres + Prometheus), and GitHub Actions: tests → build → Trivy scan (fail on HIGH/CRITICAL) → push to GHCR without stored credentials, on `main` only.

---

## Step 1: Check tool versions and choose the stack

`requirements.txt` must pin exact versions, so the latest release of each library was looked up rather than guessed:

```bash
for p in anthropic fastapi uvicorn sqlalchemy psycopg prometheus-client pydantic httpx pytest jsonschema; do
  pip index versions $p | head -1
done
```

The GitHub Actions versions were checked the same way. Each action is then pinned to its **commit SHA** instead of a tag like `v4`: a tag can be moved to point at different code, a SHA cannot. That protects the pipeline against supply-chain attacks.

```bash
git ls-remote --tags https://github.com/actions/checkout      # list the versions
git ls-remote https://github.com/actions/checkout refs/tags/v7.0.1   # get the SHA of one version
```

**Stack chosen:**

| Need | Choice | Why |
|---|---|---|
| Web API | FastAPI + Uvicorn | Simple, validates input automatically, generates `/docs` for free |
| AI | `anthropic` Python SDK, model `claude-opus-5` | The official Claude SDK, with adaptive thinking |
| Database | SQLAlchemy 2 + psycopg 3 (Postgres) / SQLite | One piece of code works with both databases |
| Argument validation | `jsonschema` | Checks the AI's tool arguments against the same schema files |
| Metrics | `prometheus-client` | The standard for Prometheus |
| Tests | `pytest` + `httpx` | `httpx` lets tests call a remote server as well |

**An early design decision:** a **MockLLM** (a fake, rule-based AI with the same message format as Claude) would be built, so the full test suite runs in CI with no API key, no cost and identical results every run.

---

## Step 2: Create the folder structure

```bash
mkdir -p conversational-agent-service/{app/tools/schemas,tests,fixtures/conversations,prometheus,.github/workflows}
cd conversational-agent-service
python -m venv .venv && source .venv/bin/activate
```

The application goes in `app/`, the tests in `tests/`, and the conversation scenarios in `fixtures/`, kept separate so they can be added without touching code.

---

## Step 3: Configuration (`app/config.py`)

All settings come from **environment variables**, so the same code runs on a laptop, in Docker and in CI. Secrets (the API key) are never written in the code.

```python
@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///dev.db"
    llm_provider: str = "anthropic"      # or "mock"
    anthropic_model: str = "claude-opus-5"
    anthropic_effort: str = "medium"     # faster replies for chat
    anthropic_max_tokens: int = 16000
    max_agent_iterations: int = 6        # safety limit on the tool loop
```

`Settings.from_env()` reads `DATABASE_URL`, `LLM_PROVIDER`, `ANTHROPIC_MODEL` and so on.

---

## Step 4: Database tables and demo data (`app/models.py`, `app/db.py`)

Three tables, defined with SQLAlchemy:

| Table | Content |
|---|---|
| `orders` | The shop's orders (ID, customer, item, status, date, tracking number) |
| `conversation_messages` | Every message of every session, stored **exactly** as exchanged with Claude (a JSON column), so the conversation can be sent back on the next turn |
| `escalations` | The human-support tickets created by `escalate_to_human` |

`app/db.py`:
- `make_engine()` connects to the database. For SQLite it allows use from several threads, because FastAPI runs requests in a thread pool.
- `init_db()` creates the tables and inserts 4 demo orders if the table is empty: `ORD-1001` shipped, `ORD-1002` processing, `ORD-1003` delivered, `ORD-1004` cancelled.

---

## Step 5: The tools (`app/tools/`)

This was done in three parts.

**5a. JSON schemas** (`app/tools/schemas/check_order_status.json`, `escalate_to_human.json`). Each file is the tool definition sent to Claude:

```json
{
  "name": "check_order_status",
  "description": "Look up the current status of a customer's order by its order ID ...",
  "strict": true,
  "input_schema": {
    "type": "object",
    "properties": {"order_id": {"type": "string", "description": "..."}},
    "required": ["order_id"],
    "additionalProperties": false
  }
}
```

The `description` matters a great deal: it's how the AI knows *when* to use the tool. `strict: true` asks Claude to always produce arguments that match the schema.

**5b. Plain functions** (`app/tools/functions.py`). They know nothing about the web or the AI:

```python
def check_order_status(order_id: str, *, db: Session) -> dict:
    order = db.get(Order, order_id.strip().upper())
    if order is None:
        return {"found": False, "order_id": ..., "message": "No order with this ID exists in the system."}
    return {"found": True, "status": order.status, ...}

def escalate_to_human(reason: str, *, db: Session, session_id: str) -> dict:
    # creates a ticket "ESC-XXXXXXXX" in the escalations table
```

**5c. The registry** (`app/tools/registry.py`) connects the two. When the AI asks for a tool, the registry:
1. finds the tool (an unknown name becomes an error result);
2. **validates** the arguments with `jsonschema` against the same schema file;
3. adds the dependencies the AI must never supply itself (the database session, the session ID);
4. runs the function, and turns any error into an error result instead of a crash.

---

## Step 6: The LLM layer (`app/llm.py`)

The Claude API reference was read first, to use the current SDK correctly. Both "engines" share the same interface: `create(system, messages, tools) -> LLMResponse(stop_reason, content)`.

**`AnthropicLLM`** calls the real Claude:

```python
response = self._client.beta.messages.create(
    model=..., max_tokens=..., system=system, messages=messages, tools=tools,
    thinking={"type": "adaptive"},                 # Claude decides how much to "think"
    output_config={"effort": "medium"},            # faster replies for chat
    betas=["server-side-fallback-2026-07-01"],
    fallbacks="default",                           # if a safety check declines, retry on a fallback model
)
content = [block.model_dump(mode="json", exclude_none=True) for block in response.content]
```

- Every block Claude returns, including "thinking" blocks, is kept **unchanged**, because it must be sent back unchanged on the next step of the tool loop.
- API errors are translated into an `LLMUnavailableError` with an HTTP code: 503 for rate limits (with `Retry-After`), 502/503 for other failures.

**`MockLLM`** is the rule-based fake used by the tests:
- The message contains an order ID (`ORD-1234`) → ask to run `check_order_status`.
- The message asks for a human, with no order ID → ask to run `escalate_to_human`.
- The message mentions "order" but gives no ID → ask for the ID.
- Anything else → "I can only help with order questions."
- After a tool result: order not found → escalate. Order found → describe its status. Escalation done → give the ticket number.

`build_llm(settings)` picks one based on `LLM_PROVIDER`.

---

## Step 7: The agent loop (`app/agent.py`)

This is the heart of the project. First, a **system prompt** (the AI's instructions): always use the tool for order status and never guess; escalate if the order doesn't exist or the customer asks for a human; do **not** escalate what's already solved (such as a delivered order); no tools for off-topic questions.

Then the loop in `Agent.chat()`:

```
load the session history from the database
add the user's message
repeat at most 6 times:
    send everything to the LLM
    if the LLM refused       → polite canned reply, stop
    if no tool was requested → the text is the final reply, stop
    otherwise run each requested tool, record it in tool_calls,
         send all the results back in ONE message, and repeat
save the whole turn in the database (one transaction)
return reply + tool_calls
```

Important details:
- The turn is **saved only at the end, in one transaction**. If Claude fails halfway, nothing half-finished is stored.
- The turn always ends with an assistant message, so the history stays valid for the next turn.
- The `agent_tool_calls_total` metric is incremented for every tool call.

---

## Step 8: Metrics (`app/metrics.py`)

```python
CHAT_REQUESTS = Counter("chat_requests_total", "...", ["status"])                 # success | error
TOOL_CALLS    = Counter("agent_tool_calls_total", "...", ["tool", "outcome"])     # success | failure
CHAT_LATENCY  = Histogram("chat_request_duration_seconds", "...", buckets=(0.05, 0.1, ..., 60))
```

The histogram buckets go up to 60 seconds because a single chat can involve several AI calls.

---

## Step 9: The web API (`app/main.py`)

A `create_app(settings)` **factory** builds the app. Tests can then create an app with their own settings (a temporary database, the mock AI). Uvicorn starts it with `uvicorn --factory app.main:create_app`.

| Endpoint | Role |
|---|---|
| `POST /chat` | Validates the input (`session_id` 1–128 chars, `message` 1–4000 chars), runs the agent, updates the metrics |
| `GET /sessions/{id}` | Message count and escalation status of a conversation (used by the tests to check the end state) |
| `GET /healthz` | "I'm alive", used by Docker's healthcheck |
| `GET /metrics` | The Prometheus metrics |

At startup (the `lifespan` function), the app creates the tables, inserts the demo data and builds the agent.

---

## Step 10: Conversation fixtures (`fixtures/`)

**10a.** First, a schema for the scenario files themselves (`fixtures/scenario.schema.json`), so a typo in a test file is caught immediately. Possible expectations: `tools_called`, `tools_not_called`, `no_tools_called`, `tool_args`, `final_reply_contains_any`, `final_reply_excludes`, `escalated`.

**10b.** Then the 5 scenarios in `fixtures/conversations/`:

| File | Scenario |
|---|---|
| `01_order_status_lookup.json` | Asks without an ID, then gives ORD-1001 → lookup, "shipped", no escalation |
| `02_unknown_order_escalates.json` | ORD-9999 doesn't exist → lookup + escalation |
| `03_unrelated_question.json` | Banana bread recipe → no tools |
| `04_delivered_order_no_escalation.json` | ORD-1003 delivered → "delivered", no escalation |
| `05_customer_requests_human.json` | Asks for a human → immediate escalation |

The expectations are deliberately **loose** (e.g. the reply contains "shipped", "on its way" *or* "in transit") so they hold for both the mock and the real Claude.

---

## Step 11: Test harness and tests (`tests/`)

**`tests/harness.py`** is the replay engine:
- `load_scenarios()` reads every JSON file and validates it against the schema.
- `replay(client, scenario)` creates a fresh session, sends each turn to `POST /chat`, collects the replies and tool calls, then reads `GET /sessions/{id}`.
- `check_outcomes(transcript, expected)` returns the list of broken expectations. On failure, the test prints them along with the full conversation.

**`tests/conftest.py`** decides *what* the tests talk to:
- If `AGENT_BASE_URL` is set, a real HTTP client points at that server (a Docker container, a deployed copy…).
- Otherwise the app starts in memory with a temporary SQLite database and `LLM_PROVIDER=mock` by default.

**The test files:**

| File | What it checks |
|---|---|
| `test_conversations.py` | One test per fixture file, plus a check that the 4 required scenarios exist |
| `test_tools.py` | The tool functions and the registry alone: found, not found, ID normalisation, bad arguments, unknown tool, ticket created |
| `test_api.py` | `/healthz`, input validation (422), unknown session (404), history kept across turns, metrics actually increasing |

---

## Step 12: Dependencies and project files

| File | Content |
|---|---|
| `requirements.txt` | Libraries needed to **run** the app, pinned (`anthropic==1.8.0`, `fastapi==0.141.1`…). This is what goes in the Docker image. |
| `requirements-dev.txt` | `-r requirements.txt` plus `pytest` and `httpx`, for **testing only**, so they stay out of the image |
| `pyproject.toml` | Project name, pytest settings, the `conversation` marker |
| `.env.example` | Template: `ANTHROPIC_API_KEY=`, `DATABASE_URL=`… (the real `.env` is ignored by git) |
| `.gitignore` | Ignores `.venv/`, `__pycache__/`, `*.db`, `.env`… |

```bash
pip install -r requirements-dev.txt
```

---

## Step 13: First test run

```bash
pytest -q
# 23 passed in 1.46s
```

Then a check that the tests **really catch mistakes**: the "delivered order" scenario was replayed with deliberately wrong expectations, and the harness reported all 4 violations. A test that can never fail is useless.

```
["expected tool 'escalate_to_human' to be called; tools used: ['check_order_status']",
 "expected no tool calls, got: ['check_order_status']",
 "final reply contains none of ['shipped']",
 "expected escalated=True, session has escalated=False"]
```

---

## Step 14: Checking the real Claude request without spending money

No API key was available, so the SDK's HTTP layer was replaced by a fake transport that **records** the request and returns a canned response. This confirmed that:
- the `anthropic-beta: server-side-fallback-2026-07-01` header is sent;
- the body contains `model`, `thinking: adaptive`, `output_config.effort`, `fallbacks: "default"`, and tools with `strict`;
- on the second loop iteration, the "thinking" and `tool_use` blocks are sent back **unchanged**, followed by the `tool_result`.

**Problem found:** version 1.x of the `anthropic` SDK uses its own HTTP library, `httpx2`, not `httpx`. The test had to use `httpx2.MockTransport`.

---

## Step 15: Dockerfile

A two-stage **multi-stage** build:

```dockerfile
# Stage 1 (builder): install the libraries into a virtual environment
FROM python:3.12-slim AS builder
RUN python -m venv /opt/venv
COPY requirements.txt .
RUN pip install -r requirements.txt && pip uninstall -y pip

# Stage 2 (runtime): copy only what's needed
FROM python:3.12-slim AS runtime
ENV DATABASE_URL="sqlite:////srv/data/agent.db" ...
RUN rm -rf .../pip* && groupadd ... && useradd --uid 10001 ... app   # remove pip, create a non-root user
COPY --from=builder /opt/venv /opt/venv
COPY app ./app
USER 10001:10001
HEALTHCHECK CMD python -c "urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
CMD ["uvicorn", "--factory", "app.main:create_app", "--host", "0.0.0.0", "--port", "8000"]
```

Why each choice:
- **Two stages:** the final image contains no build tools, so it's smaller and has fewer security holes.
- **pip removed:** it isn't needed at runtime, and it is frequently flagged by scanners.
- **Non-root user:** limits the damage if someone breaks in.
- **`/srv/data`:** a writable folder for the SQLite fallback, since the non-root user can't write anywhere else.
- **`.dockerignore`:** keeps tests, `.env` and `.git` out of the image.

---

## Step 16: Docker Compose, Prometheus and the Makefile

`docker-compose.yml` defines 3 services:
- `api`: built from the Dockerfile, with `DATABASE_URL` pointing at the `postgres` service and the API key read from `.env`. It waits until Postgres is healthy (`depends_on: condition: service_healthy`).
- `postgres`: `postgres:17-alpine`, with a `pg_isready` healthcheck and a volume so data survives restarts.
- `prometheus`: `prom/prometheus:v3.5.0`, configured by `prometheus/prometheus.yml` to scrape `api:8000/metrics` every 15 seconds.

The `Makefile` adds shortcuts: `make test`, `make test-live`, `make test-running`, `make run`, `make up`, `make down`.

---

## Step 17: Testing the container (SQLite, then Postgres)

```bash
docker build -t cas:ci .
docker run -d --name cas -p 8000:8000 -e LLM_PROVIDER=mock cas:ci
curl localhost:8000/healthz                      # {"status":"ok"}
docker exec cas id                               # uid=10001(app) → not root ✔
docker exec cas python -c "import pip"           # ModuleNotFoundError → pip removed ✔
AGENT_BASE_URL=http://localhost:8000 pytest tests/test_conversations.py tests/test_api.py
# 11 passed
docker inspect --format '{{.State.Health.Status}}' cas   # healthy ✔
```

Then again with a real Postgres database:

```bash
docker run -d --name pg -e POSTGRES_USER=agent -e POSTGRES_PASSWORD=agent -e POSTGRES_DB=agent postgres:17-alpine
docker run -d --name cas -e LLM_PROVIDER=mock \
  -e DATABASE_URL=postgresql+psycopg://agent:agent@<postgres-host>:5432/agent cas:ci
AGENT_BASE_URL=http://localhost:8000 pytest tests/test_conversations.py tests/test_api.py   # 11 passed
docker exec pg psql -U agent -d agent -c "select ticket_id, reason from escalations"      # tickets are stored ✔
```

**Problems met in the build environment:**
- *The Docker daemon wasn't running* → started it (`dockerd`).
- *Docker Hub replied `429 Too Many Requests`* (rate limit) → pulled the same official images through Google's mirror, `mirror.gcr.io/library/python:3.12-slim`.
- *The `# syntax=docker/dockerfile:1` line* forced an extra download that was blocked. It isn't needed, so it was removed.

---

## Step 18: Security scan with Trivy

```bash
trivy image --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 cas:ci
```

Result: **0 HIGH/CRITICAL vulnerabilities** on the operating system (Debian 13) and on every Python library.

`--ignore-unfixed` makes the scan fail only on vulnerabilities that **have a fix available**. Without it, the build could stay red because of a problem no one can fix yet.

---

## Step 19: CI/CD workflow (`.github/workflows/ci.yml`)

Three jobs, one after another:

```
test ──► image ──► publish (push to main only)
```

1. **`test`:** checkout → Python 3.12 (with a pip cache) → `pip install -r requirements-dev.txt` → `pytest -v` with `LLM_PROVIDER=mock`.
2. **`image`:**
   - build the image with `docker/build-push-action`, using a build cache (`type=gha`);
   - start the container, wait for `/healthz`, then **replay the conversation tests against it** over HTTP;
   - print the container logs (always, which helps debugging);
   - Trivy scan (`aquasecurity/trivy-action`): HIGH/CRITICAL with a fix → failure;
   - on `main`: `docker save` the image and upload it as an artifact.
3. **`publish`** (push to `main` only):
   - download the artifact and `docker load` the **exact image that was scanned**;
   - log in to `ghcr.io` with the temporary `GITHUB_TOKEN`;
   - push the `sha-<commit>` and `latest` tags;
   - `actions/attest-build-provenance` creates a provenance attestation signed through **OIDC** (Sigstore).

Security choices:
- `permissions: contents: read` by default. Only `publish` gets `packages: write`, `id-token: write` and `attestations: write` (**least privilege**).
- Every action is pinned to a commit SHA.
- `persist-credentials: false` on checkout.
- No stored password: GHCR doesn't accept OIDC login directly, so the push uses the job's temporary token, and OIDC is used for the signature.

Validating the workflow file before pushing:

```bash
pip install actionlint-py
actionlint .github/workflows/ci.yml     # no errors
```

---

## Step 20: Documentation

- `README.md`: technical documentation, with Mermaid architecture diagrams (flow and sequence), the fixture format, commands, metrics, CI/CD and configuration.
- `GUIDE.md`: the beginner's guide.
- `BUILD_STEPS.md`: this file.

---

## Step 21: Git commit and packaging as a zip

```bash
# final check
pytest -q                                  # 23 passed
find . -name __pycache__ -prune -exec rm -rf {} +

# git repository
git init -b main
git add -A
git commit -m "Initial commit: conversational-agent-service"

# zip archive
cd ..
zip -qr conversational-agent-service.zip conversational-agent-service
```

To publish it on GitHub (this triggers the CI/CD from step 19):

```bash
git remote add origin https://github.com/<your-github-name>/conversational-agent-service.git
git push -u origin main
```

---

## Summary of the verification commands

| What | Command | Expected result |
|---|---|---|
| All tests (mock) | `pytest` | `23 passed` |
| Scenarios only | `pytest -m conversation -v` | `5 passed` (one per scenario) |
| Against real Claude | `LLM_PROVIDER=anthropic pytest -m conversation -v` | Pass (needs `ANTHROPIC_API_KEY`) |
| Against a running server | `AGENT_BASE_URL=http://localhost:8000 pytest tests/test_conversations.py tests/test_api.py` | `11 passed` |
| Build the image | `docker build -t cas:ci .` | Image built |
| Non-root | `docker exec cas id` | `uid=10001(app)` |
| Health | `docker inspect --format '{{.State.Health.Status}}' cas` | `healthy` |
| Security | `trivy image --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 cas:ci` | 0 vulnerabilities, exit code 0 |
| Workflow | `actionlint .github/workflows/ci.yml` | No errors |
| Full stack | `docker compose up --build` | API on :8000, Prometheus on :9090 |
