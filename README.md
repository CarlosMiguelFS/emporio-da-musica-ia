# Empório da Música: Customer Service Agent

Prototype of a text chat agent for Empório da Música, a (fictional) musical instrument store in
Campo Grande/MS. It answers customers in Portuguese, looks up the store data (products, stock,
promotions, orders) and the policy manual, shows a catalog the customer can pick from, and sends
the confirmed order to a store attendant, who creates the payment link.

## Running

Requires **Python 3.11+** (some pinned libraries don't install on 3.10) and a Gemini API key
(<https://aistudio.google.com/apikey>).

Quick start (Windows PowerShell; on Linux/macOS use `source .venv/bin/activate` and `cp`):

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt     # or requirements-dev.txt to also get pytest and ruff
copy .env.example .env              # then open .env and paste your Gemini key
streamlit run app.py                # then open http://localhost:8501 in the browser
```

The browser doesn't open by itself (the app runs headless, see `.streamlit/config.toml`).
The `.env` file is git-ignored, so the key never goes to the repository. In it, `MODELS` is
optional: if the first model is down, the next one answers.

Other commands:

```bash
streamlit run app.py                # chat in the browser, with catalog and cart buttons
python main.py                      # chat in the terminal (catalog as a numbered list)
python main.py --debug --save       # shows the tools called / saves the chat in conversations/
python mcp_server.py                # the same tools as an MCP server (stdio)
python -m emporio.observability     # summary of the logs
pytest                              # tests, no API key needed
python -m evals.run_evals --runs 1  # evaluation against the real model (uses the API)
```

## How it works

```
customer message
  -> input guardrails (injection? personal data? too long? server busy?)   blocked -> fixed answer
  -> Gemini (store persona + rules)
       |-- store policy question -> search_policies (retrieval over the manual)
       |-- data question         -> search_products / active_promotions / get_order / installment_options
       |-- wants to buy          -> show_catalog / add_to_cart / change_quantity / show_cart / confirm_order
  -> output guardrails (prices must come from a tool, no links, no prompt leak, personal data masked)
  -> answer + catalog or cart for the screen + one log line
```

```
├── app.py / main.py / mcp_server.py   browser, terminal and MCP: all call the same agent
├── emporio/
│   ├── agent.py        Gemini, model fallback, the reply pipeline
│   ├── prompt.py       persona and rules
│   ├── tools.py        data tools (catalog search, promotions, orders, installments)
│   ├── rag.py          search over the policy manual
│   ├── cart.py         catalog, cart, order handoff (no UI code: every channel uses it)
│   ├── guardrails.py   input and output checks
│   └── observability.py, data.py, settings.py, chat_ui.py, text_utils.py
├── data/               the CSVs and the manual as received, plus politicas.md (manual as text)
├── tests/              159 tests, no API needed
├── evals/              real conversations against the model and the last report
└── conversations/      5 example conversations
```

## The five pillars

| Pillar | Where | What it does |
|---|---|---|
| Agent | `agent.py`, `tools.py`, `cart.py` | Function calling: the model picks the tool, Python runs it. Max 5 tool calls per message. |
| RAG | `rag.py` | The manual is split by section, matched by keywords (no accents, simple stemming, title weighted); the top 3 go to the model. |
| Guardrails | `guardrails.py`, `cart.py` | Injection patterns, personal data masking, prices checked against tool results, no order sent without the customer's confirmation. See [SECURITY.md](SECURITY.md). |
| MCP | `mcp_server.py` | The same tools as an MCP server, so another app or agent can use the store without new code. |
| Observability | `observability.py` | One JSON line per turn: tools, tokens, latency, guardrail events, errors. |

## Technical decisions

- **Hybrid agent: function calling for the data, retrieval for the manual.** The data questions
  are always the same few kinds, so small Python functions cover them and can't build a wrong
  query (no SQL agent). The manual search is one more tool, so the model decides *when* to read
  the policies, and that shows up in the logs.
- **Keyword retrieval, not embeddings.** The manual has 25 sections: keywords are enough, have no
  extra cost and always give the same result, which makes them easy to test.
- **Calculations in Python.** Discounts, PIX price, installments, days since delivery and cart
  totals use `Decimal` in the code. A wrong price is what the manual most wants to avoid (7.1),
  so the output guardrail blocks any value no tool returned.
- **Gemini Flash-Lite** (`google-genai`): cheap, fast, good Portuguese, function calling. A model
  list with fallback, because a model was retired for new keys during development.
- **Low variation:** temperature 0, fixed seed, minimal thinking, 800 output tokens.
- **Prompt:** instructions in English, answers in Portuguese. Persona from the manual (7.1), warm
  and short for a phone chat, one question per message, at most one recommendation taken from the
  product description, never a list of products in the text when the catalog is on the screen.
- **Catalog and cart, separated from the screen.** `cart.py` has no UI code: the browser shows the
  catalog as buttons, the terminal and MCP as a numbered list ("quero o 2"). The catalog follows
  WhatsApp's multi-product message (sections by category, at most 30 products); with more than 10
  the customer first gets a summary by brand. The cart adds up across catalogs, the customer
  checks it before confirming, and changes after confirming warn the team right away.
- **Interface:** Streamlit, because the challenge is about the agent, not the frontend. The answer
  waits a moment that grows with its size, like someone typing. Errors never reach the screen.
- **History:** in memory, one conversation per browser tab. Persisting it only makes sense with a
  real channel and customer ids.
- **Data treatment:** once at startup in `data.py`: active promotions only (biggest discount wins),
  accents ignored in search, status translated, and `customers.csv` loaded without phone and e-mail.

## Evaluation

Unit tests check the code; they can't tell if the model behaves. `evals/` runs 35 real
conversations against the model and checks each answer: the tools it should (and shouldn't) call,
what the answer must (and must not) contain, chat rules (one question, short, Brazilian price
format, no emojis), the cart contents and that no order is sent without confirmation.
Last run: 34 of 35 cases passed. The miss was real: in one complaint the answer gave the contacts but not the 24 business hours the manual promises ([report](evals/results/latest.md)).

The evaluation caught real problems: promotions without the discount percent, prices without the
thousands separator, the store's own e-mail being masked, products listed twice (text and
catalog), an empty answer after two tool calls, and the model adding a product with a made-up id (a violão went in instead of a guitar). Manual tests caught two more: the cart counting
a screen pick twice and a cold, ticket-closing tone. Each one became a test or an eval case.

## Assumptions

- **"Help the team":** the agent answers the recurring questions itself and hands to the team
  what needs a person: payment links, complaints, defects, anything it can't confirm.
- **Language:** code and docs in English (submission instructions), chat in Portuguese (the store,
  its customers and the manual are in Portuguese).
- **Order access:** order number + first name of the buyer (manual section 9). Not real
  authentication; see Limitations.
- **Return window:** the data has no real delivery date, so `estimated_delivery` is used.
- **Installments:** the detailed rules in 3.1 (R$ 50 minimum up to 3x) win over the summary table.
- **Contacts:** the manual has two phones and two spellings of the e-mail. The agent uses
  (67) 3341-4444, which appears twice, and the e-mail without accent.
- **Order handoff:** the challenge has no attendant system, so a confirmed order is written to
  `logs/team/orders.jsonl` (products and values only) for the team to read.

## Data issues found

- Some names don't match the description ("Yamaha Kit 1" is described as a Pearl Export).
- Product 136 (bass) costs R$ 441, far below the rest of the category; product 130 is
  `coming_soon` with stock; order 8 is still `shipped` with a February estimate.

## Limitations and next steps

- First name is not authentication. Next: a code sent to the customer's registered phone.
- Injection detection is pattern-based; the other layers limit the damage (see SECURITY.md).
- Limits (order tries, messages per minute) live in memory and reset on restart. Next: Redis and
  a limit per IP.
- The handoff is a file, not a queue with acknowledgement. Next: a ticket in the store's system.
- Holidays are not in the data.
- The evaluation checks rules, not the quality of the tone. Next: an LLM-as-judge rubric and cases
  taken from real conversations in the logs.
- Next: WhatsApp integration (the store's main channel) using the same `cart.py`.

## Use of AI coding assistants

I used Claude Code (Anthropic) in VS Code as a pair programmer. I described what I wanted, it
wrote the implementation, and I ran it, tested it as a customer and asked for changes until it
behaved the way I wanted. The architecture (function calling + retrieval) was its suggestion; I
agreed after going through the trade-offs. What came from my side:

- The constraints: simple code in the style of my previous projects, English for the submission,
  the cheapest model, no emojis or long dashes in the answers.
- The features after testing the first version: guardrails, RAG, MCP, observability, and a
  WhatsApp-style catalog with a cart, because a list of names in a chat was hard to choose from.
- The bugs I found talking to the agent: answers too long, the missing "$" in the UI, the cart
  counting a screen pick twice, an empty answer, products listed twice and a cold tone.
- The question that shaped the MCP part: "would this work where there are no buttons?"
- A security review that I asked for after the features were done. It found an order lookup
  brute force, the model being able to confirm an order alone, and a price trick; all fixed, with
  a test for each attack.
- Before each commit, I had the assistant replay the commit order in a copy of the repository and
  run the tests at every step.
