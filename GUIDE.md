# Beginner's Guide to conversational-agent-service

This guide is written for someone with **no background in AI or DevOps**. It explains what the project is, how it works, and how to run and test it yourself, step by step.

> To see how the project was built from an empty folder, step by step, read [BUILD_STEPS.md](BUILD_STEPS.md).

## Contents

1. [What the project does](#1-what-the-project-does)
2. [Vocabulary](#2-vocabulary)
3. [What happens when a customer sends a message](#3-what-happens-when-a-customer-sends-a-message)
4. [Tour of the files](#4-tour-of-the-files)
5. [The testing idea (the most important part)](#5-the-testing-idea-the-most-important-part)
6. [Monitoring (metrics)](#6-monitoring-metrics)
7. [Docker (packaging)](#7-docker-packaging)
8. [CI/CD (the GitHub robots)](#8-cicd-the-github-robots)
9. [Hands-on: run it yourself, step by step](#9-hands-on-run-it-yourself-step-by-step)
10. [Experiments to help you understand](#10-experiments-to-help-you-understand)
11. [Common problems](#11-common-problems)
12. [Explaining it in an interview](#12-explaining-it-in-an-interview)

---

## 1. What the project does

Picture an online shop with a chat window where customers ask "Where is my order?". This project is the program behind that chat window. Three things make it more than a simple chatbot:

1. **It uses AI (Claude) to understand the customer.** People write in all sorts of ways ("where's my stuff?", "my package ORD-1001?"), and the AI understands all of them.
2. **The AI can take actions.** It doesn't only talk. It can look up an order in a database, or hand the customer over to a human.
3. **It is built the way a company would run it in production.** It has automatic tests, monitoring, packaging in a container, and a pipeline that checks and publishes every change automatically. This is the "DevOps" part.

The shop itself is deliberately tiny: 4 fake orders. The point is to show everything *around* the AI.

---

## 2. Vocabulary

| Word | Meaning |
|---|---|
| **LLM** (Large Language Model) | An AI that reads and writes text. Claude is one. You send it text and it sends text back. |
| **API** | A way for one program to talk to another over the internet using fixed rules. Claude is used through Anthropic's API, and our project also *offers* its own API. |
| **Endpoint** | One specific "door" of an API, e.g. `POST /chat` means "send a chat message here". |
| **Agent** | An AI that can decide to **use tools** (do actions), not just reply with text. |
| **Tool** | A normal function in our code that the AI is allowed to ask us to run, e.g. `check_order_status`. |
| **JSON** | A text format for data, like `{"order_id": "ORD-1001"}`. Used everywhere here. |
| **JSON schema** | A description of what valid JSON must look like (e.g. "must contain `order_id`, which is text"). |
| **Database** | Where data is stored permanently. We use **Postgres** (a real database server) or **SQLite** (a database in a single file, handy for testing). |
| **SQLAlchemy** | A Python library for talking to databases without writing raw SQL. |
| **FastAPI** | A Python library for building web APIs quickly. |
| **Uvicorn** | The web server that runs our FastAPI app. |
| **Environment variable** | A setting given to a program from outside its code, e.g. `LLM_PROVIDER=mock`. Keeps secrets out of the code. |
| **Test** | Code that checks other code works. **pytest** is the Python tool that runs tests. |
| **Fixture file** | Here, a JSON file describing a fake conversation plus what should happen. |
| **Mock** | A fake replacement for something real. Our "MockLLM" pretends to be Claude so tests don't need internet or money. |
| **Docker / container** | A way to package a program *with everything it needs* so it runs the same on any computer. An **image** is the package, and a **container** is a running copy of it. |
| **Docker Compose** | A tool that starts several containers together (our app + database + monitoring). |
| **Metrics** | Numbers about the running app: how many requests, how fast, how many errors. |
| **Prometheus** | A tool that collects metrics every few seconds and lets you query and graph them. |
| **CI/CD** | *Continuous Integration / Continuous Delivery*. Every time code changes, robots automatically test it, build it and publish it. |
| **GitHub Actions** | GitHub's CI/CD robots. They follow the instructions in `.github/workflows/ci.yml`. |
| **Registry (GHCR)** | An online store for Docker images. GHCR is the GitHub Container Registry. |
| **Trivy** | A scanner that looks for known security holes in your image. |
| **CVE** | A publicly known security hole, rated LOW / MEDIUM / HIGH / CRITICAL. |
| **OIDC** | A way for GitHub robots to prove who they are *without a stored password*. |

---

## 3. What happens when a customer sends a message

Say the customer types **"Where is my order ORD-1001?"**. Here is the path the message takes:

```
Customer ──► POST /chat ──► Agent ──► Claude
                              ▲          │ "please run check_order_status(ORD-1001)"
                              │          ▼
                              │     Tool runs → database → {"status": "shipped", ...}
                              │          │
                              └── result sent back to Claude
                                         │ "Your order has shipped! Tracking: 1Z999..."
Customer ◄── reply + list of tools used ◄┘
```

Step by step:

1. **The customer's app sends** `{"session_id": "abc", "message": "Where is my order ORD-1001?"}` to `POST /chat`. The `session_id` is the conversation's name, so the agent remembers earlier messages.
2. **`app/main.py`** receives it and checks it's valid (the message isn't empty, etc.).
3. **`app/agent.py`** loads the earlier messages of session `abc` from the database. It sends Claude the whole conversation, some instructions (the "system prompt": *"You are a support assistant, never guess an order status…"*) and the list of tools Claude is allowed to use.
4. **Claude replies**: "I want to call `check_order_status` with `order_id = ORD-1001`." Claude *cannot* touch the database itself. It only *asks* us to.
5. **`app/tools/registry.py`** checks the request is well formed (using the JSON schema), then runs the real Python function in **`app/tools/functions.py`**, which reads the database.
6. The result (`shipped`, with a tracking number) is **sent back to Claude**, which now writes a friendly reply.
7. This back-and-forth can repeat, up to 6 times per message. For example: order not found → Claude then asks to run `escalate_to_human`.
8. The whole exchange is **saved in the database**, and the customer receives:

```json
{
  "session_id": "abc",
  "reply": "Your order ORD-1001 (Wireless headphones) has shipped and is on its way...",
  "tool_calls": [
    {"name": "check_order_status", "input": {"order_id": "ORD-1001"},
     "output": {"found": true, "status": "shipped", "...": "..."}, "is_error": false}
  ]
}
```

The `tool_calls` list in the response matters a lot: it's what makes the agent **testable** (see section 5).

---

## 4. Tour of the files

```
conversational-agent-service/
├── app/                        ← the application
│   ├── main.py                 the "doors": /chat, /sessions/{id}, /healthz, /metrics
│   ├── agent.py                the brain loop: Claude ↔ tools, plus the system prompt
│   ├── llm.py                  two "AI engines": the real Claude, and MockLLM (fake)
│   ├── config.py               reads settings from environment variables
│   ├── models.py               database tables: orders, messages, escalations
│   ├── db.py                   database connection + the 4 demo orders
│   ├── metrics.py              definitions of the Prometheus numbers
│   └── tools/
│       ├── functions.py        the real actions (look up order, create ticket)
│       ├── registry.py         checks the AI's requests, then runs the right function
│       └── schemas/*.json      tool descriptions (sent to Claude AND used for checks)
├── fixtures/
│   ├── scenario.schema.json    rules for writing a conversation test file
│   └── conversations/*.json    the 5 fake conversations + expected results
├── tests/                      ← pytest tests
│   ├── conftest.py             prepares the app (or a remote URL) for the tests
│   ├── harness.py              replays a conversation and checks the results
│   ├── test_conversations.py   one test per conversation file
│   ├── test_tools.py           tests the tools alone (no AI, no web)
│   └── test_api.py             tests the web API and the metrics
├── Dockerfile                  recipe for building the container image
├── docker-compose.yml          starts app + Postgres + Prometheus together
├── prometheus/prometheus.yml   tells Prometheus where to collect metrics
├── .github/workflows/ci.yml    instructions for the GitHub robots (CI/CD)
├── requirements.txt            exact list of libraries the app needs
├── requirements-dev.txt        extra libraries for testing (pytest…)
├── pyproject.toml              project info + pytest settings
├── .env.example                template for your secret settings (API key)
├── .gitignore / .dockerignore  files git / Docker should ignore
├── Makefile                    shortcuts: make test, make up…
├── README.md                   technical documentation
├── GUIDE.md                    this guide
└── BUILD_STEPS.md              how the project was built, step by step
```

**Demo data in the database:**

| Order | Status |
|---|---|
| ORD-1001 | shipped |
| ORD-1002 | processing |
| ORD-1003 | delivered |
| ORD-1004 | cancelled |
| anything else (e.g. ORD-9999) | does not exist → should escalate to a human |

**Why tools are "plain functions + JSON schema".** The function `check_order_status` knows nothing about AI or the web. It takes an ID and returns a result, so it can be tested alone like any normal code. The JSON schema file does two jobs: it tells Claude how to use the tool, and it's used to double-check what Claude sends. One source of truth means the two can't drift apart.

---

## 5. The testing idea (the most important part)

**The problem:** AI answers are never exactly the same. Ask twice and you get *"Your order has shipped!"* one time and *"Good news, ORD-1001 is on its way."* the next. A classic test like `reply == "Your order has shipped!"` would fail randomly.

**The solution: test the outcome, not the words.** We check what the agent *did*:
- Which tools were called, and which were **not** called.
- With which arguments (e.g. `order_id = ORD-1001`).
- The end state in the database (was a human ticket created?).
- Loose keywords in the final reply (e.g. contains "delivered").

Each conversation is a JSON file. Example, `04_delivered_order_no_escalation.json`:

```json
{
  "name": "delivered_order_no_escalation",
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

In plain words: *"When a customer asks about a delivered order, the agent must look it up, must say 'delivered', and must NOT bother a human."*

The 5 scenarios:

| # | Customer says | Expected behaviour |
|---|---|---|
| 1 | "Where's my order?" then "ORD-1001" | Asks for the ID, looks it up, says "shipped", no escalation |
| 2 | "Status of ORD-9999?" | Looks it up, finds nothing, **escalates** to a human |
| 3 | "Banana bread recipe?" | **No tool** used at all |
| 4 | "Where is ORD-1003?" | Says "delivered", **no** escalation |
| 5 | "Put me through to a human" | Escalates right away without looking anything up |

**Three ways to run the same tests** (all the same files, just different targets):

| Mode | What it tests | Needs |
|---|---|---|
| **Mock** (default) | All the code, using the fake AI | Nothing: free, fast, identical every time |
| **Real Claude** | The real AI's behaviour | An API key (costs a few cents) |
| **Running server** | A live deployed copy (e.g. the Docker container) | The server running |

Why a mock at all? The CI robots run the tests on *every* change. Calling the real AI each time would cost money, need a secret key, and give random results. MockLLM is a small rule-based program: if it sees an order ID it asks to look it up, if it sees "human" it asks to escalate, and so on. It answers in exactly the same *format* as Claude, so all the real code gets exercised.

---

## 6. Monitoring (metrics)

When an app is running, you want to know what it's doing. `GET /metrics` returns numbers like:

```
chat_requests_total{status="success"} 10.0
agent_tool_calls_total{outcome="success",tool="check_order_status"} 6.0
agent_tool_calls_total{outcome="success",tool="escalate_to_human"} 3.0
chat_request_duration_seconds_bucket{le="1.0"} 8.0
```

| Metric | Question it answers |
|---|---|
| `chat_requests_total` | How many messages did we handle, and how many failed? |
| `agent_tool_calls_total` | Which tools does the AI use, and do they fail? A sudden rise in escalations is a warning sign. |
| `chat_request_duration_seconds` | How long do customers wait? (A **histogram** groups the waits into buckets: under 1s, under 2.5s…) |

**Prometheus** reads this page every 15 seconds and keeps the history, so you can draw graphs over time.

---

## 7. Docker (packaging)

"It works on my machine" is a classic problem. Docker fixes it by packaging the app with Python and all its libraries into an **image**. The `Dockerfile` has two stages (a **multi-stage** build):

1. **Builder stage:** installs all the libraries.
2. **Final stage:** copies *only* the result into a clean, small image. The installation tools (pip) are deleted, which leaves less to attack.

Security details that interviewers like:
- The app runs as a **non-root user** (uid 10001). If someone breaks in, they don't get admin rights.
- A **HEALTHCHECK** lets Docker check the app is alive by calling `/healthz`.

`docker-compose.yml` starts 3 containers together:
- `api`: our app, on port 8000.
- `postgres`: the database, on port 5432.
- `prometheus`: monitoring, on port 9090.

---

## 8. CI/CD (the GitHub robots)

Every time you push code to GitHub, the robots in `.github/workflows/ci.yml` do this:

```
push ──► 1. test ──► 2. image ──► 3. publish (only on the main branch)
```

1. **test:** installs the libraries and runs all 23 tests with the mock AI. If anything fails, it stops here.
2. **image:**
   - builds the Docker image;
   - **starts it**, then replays the conversation tests *against the running container*, which proves the packaged app really works;
   - **scans it with Trivy.** If there is a HIGH or CRITICAL security hole that has a fix available, the pipeline fails and nothing is published.
3. **publish** (only on `main`):
   - pushes the **exact image that was scanned** to `ghcr.io/<your-github-name>/conversational-agent-service`;
   - adds a signed "birth certificate" (a **provenance attestation**) proving which code and which workflow built it.

**"No stored credentials":** there is no password saved anywhere. GitHub gives the robot a temporary token that expires when the job ends. The signature uses **OIDC**: GitHub vouches for the robot's identity, so no secret key is needed. Also, every GitHub Action is pinned to an exact version fingerprint (a commit SHA) so nobody can swap in malicious code.

---

## 9. Hands-on: run it yourself, step by step

### Step 0: Install the prerequisites (once)

| Tool | Where to get it | Check it works |
|---|---|---|
| Python 3.12 | python.org (on Windows, tick "Add to PATH") | `python --version` |
| Git | git-scm.com | `git --version` |
| Docker Desktop (for step 5) | docker.com | `docker --version` |

### Step 1: Unzip and create a Python environment

```bash
unzip conversational-agent-service.zip
cd conversational-agent-service

python -m venv .venv              # a private Python "box" for this project
source .venv/bin/activate         # Mac/Linux
# .venv\Scripts\activate          # Windows (PowerShell or cmd)

pip install -r requirements-dev.txt   # installs all libraries (~1 minute)
```

When the environment is active, your prompt shows `(.venv)`.

### Step 2: Run the tests (no API key needed)

```bash
pytest
```

Expected output: `23 passed in ~2s`. That run covered the tools, the API, the metrics and the 5 conversations, all with the mock AI.

For more detail:

```bash
pytest -v                          # the name of every test
pytest -m conversation -v          # only the 5 conversation scenarios
pytest tests/test_tools.py -v      # only the tool unit tests
```

### Step 3: Start the server and talk to it

```bash
# Mac/Linux:
LLM_PROVIDER=mock uvicorn --factory app.main:create_app --reload
# Windows PowerShell:
$env:LLM_PROVIDER="mock"; uvicorn --factory app.main:create_app --reload
```

Leave it running. The easiest way to test it is **in your browser**:
1. Open **http://localhost:8000/docs**. This interactive page is generated automatically by FastAPI.
2. Click `POST /chat`, then **Try it out**, and paste:
   ```json
   {"session_id": "demo", "message": "Where is my order ORD-1001?"}
   ```
3. Click **Execute** and read the reply and `tool_calls`.

Or from a second terminal (Mac/Linux):

```bash
curl -s localhost:8000/chat -H 'content-type: application/json' \
  -d '{"session_id":"demo","message":"Where is my order ORD-9999?"}'

curl -s localhost:8000/sessions/demo     # message count + escalated true/false
curl -s localhost:8000/metrics | grep -E '^(chat_|agent_)'
curl -s localhost:8000/healthz           # {"status":"ok"}
```

Messages to try: `ORD-1003` (delivered), `ORD-1002` (processing), `"I want a human"`, `"What's the weather?"`.

Stop the server with **Ctrl+C**.

### Step 4: Use the real Claude AI

1. Create an API key at **console.anthropic.com** (it needs a little credit).
2. Put the key in your terminal session:
   ```bash
   export ANTHROPIC_API_KEY=sk-ant-...        # Mac/Linux
   # $env:ANTHROPIC_API_KEY="sk-ant-..."      # Windows PowerShell
   ```
   When you run the app directly with Python, it does **not** read the `.env` file by itself, so you must set the key in the terminal like this. Docker Compose does read `.env` (step 5).
3. Run the same 5 conversations against real Claude:
   ```bash
   LLM_PROVIDER=anthropic pytest -m conversation -v
   ```
4. Or chat with it:
   ```bash
   uvicorn --factory app.main:create_app --reload   # anthropic is the default
   ```
   Replies are now written by Claude, so the wording changes every time, but the tests still pass. That's the whole idea.

### Step 5: The full stack with Docker (app + Postgres + Prometheus)

```bash
cp .env.example .env
# edit .env and add ANTHROPIC_API_KEY=sk-ant-...   (or skip it and use mock below)

docker compose up --build                     # real Claude
# LLM_PROVIDER=mock docker compose up --build # no key needed
```

Then open:
- **http://localhost:8000/docs**: the app, now saving data in Postgres.
- **http://localhost:9090**: Prometheus. Send a few chats, then type these in the query box:
  - `chat_requests_total`
  - `sum by (tool, outcome) (agent_tool_calls_total)`
  - `histogram_quantile(0.95, rate(chat_request_duration_seconds_bucket[5m]))` (95% of requests are faster than this)

In another terminal, replay the tests against the running stack:

```bash
AGENT_BASE_URL=http://localhost:8000 pytest tests/test_conversations.py tests/test_api.py -v
```

Stop everything with `docker compose down`. Add `-v` to also delete the database.

### Step 6: Put it on GitHub and watch the robots

1. On github.com, create a new **empty** repository named `conversational-agent-service`.
2. Push the code:
   ```bash
   git remote add origin https://github.com/<your-github-name>/conversational-agent-service.git
   git push -u origin main
   ```
3. Open the **Actions** tab to watch the 3 jobs run: test → image → publish.
4. Once they finish, your image appears under **Packages** on your profile. Set it to public so recruiters can see it.

### Shortcuts (Mac/Linux, with `make`)

```bash
make test          # = pytest
make run           # start the server
make test-live     # tests against real Claude
make up / make down  # docker compose
```

---

## 10. Experiments to help you understand

1. **Make a test fail on purpose.** In `fixtures/conversations/04_delivered_order_no_escalation.json`, change `"escalated": false` to `true`, then run `pytest -m conversation`. The test fails and prints exactly what went wrong plus the full conversation. Undo the change afterwards.
2. **Add your own scenario.** Copy a fixture file, rename it `06_cancelled_order.json`, and ask about `ORD-1004` with `"final_reply_contains_any": ["cancelled"]`. It's picked up automatically, with no code to write.
3. **Add a new order.** Add an entry to `SEED_ORDERS` in `app/db.py`, delete `dev.db`, and restart.
4. **Watch the metrics move.** Open `/metrics`, send 3 chats, refresh, and see the counters go up.

---

## 11. Common problems

| Problem | Fix |
|---|---|
| `pytest: command not found` / `No module named ...` | The environment isn't active. Run `source .venv/bin/activate` (or `.venv\Scripts\activate` on Windows). |
| Error 502/503, or an authentication error from `/chat` | No valid API key. Set `ANTHROPIC_API_KEY`, or use `LLM_PROVIDER=mock`. |
| `address already in use` on port 8000 | Something else is using the port. Stop it, or add `--port 8001`. |
| Docker errors about the daemon | Docker Desktop isn't running. Start it. |
| `429 Too Many Requests` when Docker pulls images | Docker Hub rate limit. Run `docker login`, or wait and retry. |
| Old data showing up | Delete `dev.db` (local run) or run `docker compose down -v` (Docker). |

---

## 12. Explaining it in an interview

**30-second pitch:**

> "I built a Claude-based support agent with tool calling. Each tool is a plain, unit-tested function, and the same JSON schema describes it to the LLM and validates its arguments. Because LLM output isn't deterministic, I test **outcomes** instead of wording: multi-turn conversations stored as JSON fixtures are replayed against the API, and I assert which tools were called, with which arguments, and the final database state. CI runs them with a deterministic mock, then replays them against the built container, gates on a Trivy scan, and publishes to GHCR with no stored credentials: an ephemeral token, plus an OIDC-signed provenance attestation. The service exposes Prometheus metrics for request volume, tool success and failure, and latency."

**Likely follow-up questions, and your answers:**

- **"Why a mock?"** Speed, zero cost, no secret key in CI, and repeatable results. The same fixtures can still run against real Claude on demand.
- **"How would you deploy it?"** On Kubernetes (e.g. AKS), with the image from GHCR, `DATABASE_URL` and the API key stored as secrets, and `/healthz` as the liveness probe.
- **"What would you add next?"** A scheduled nightly run of the fixtures against real Claude to catch behaviour changes, Grafana dashboards, and alerts when the escalation rate jumps.
- **"What if the AI calls a tool with bad arguments?"** The registry validates them against the JSON schema first. A bad call becomes an error result that is sent back to the AI, which can then correct itself. The request never crashes.
- **"What if Claude's API is down?"** The endpoint returns HTTP 502/503 (passing `Retry-After` through on rate limits), and nothing half-finished is saved to the database.
