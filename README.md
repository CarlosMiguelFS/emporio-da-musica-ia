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
python main.py --debug --save       # shows the tools called / saves the chat, masked, in logs/
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
├── app.py / main.py    browser and terminal: both call the same agent
├── mcp_server.py      optional: the same tools for other MCP clients
├── emporio/
│   ├── agent.py        Gemini, model fallback, the reply pipeline
│   ├── prompt.py       persona and rules
│   ├── tools.py        data tools (catalog search, promotions, orders, installments)
│   ├── rag.py          search over the policy manual
│   ├── cart.py         catalog, cart, order handoff (no UI code: every channel uses it)
│   ├── guardrails.py   input and output checks
│   └── observability.py, data.py, settings.py, chat_ui.py, text_utils.py
├── data/               the CSVs and the manual as received, plus politicas.md (manual as text)
├── tests/              255 tests, no API needed
├── evals/              real conversations against the model and the last report
└── conversations/      5 example conversations
```

## How it answers the store's problem

The team spends its day on the same questions: opening hours, order status, price and stock.
The agent answers those by itself and hands to the team what needs a person.

| Part | Where | What it does |
|---|---|---|
| Agent | `agent.py`, `prompt.py` | Gemini with function calling: the model picks the tool, Python runs it (at most 5 per message). Persona from the manual (7.1). |
| Data lookups | `tools.py`, `cart.py` | Products, promotions, orders, installments, catalog and cart. Values are computed in Python, not by the model. |
| Policies | `rag.py` | Keyword search over the 25 sections of the manual; the top 3 go to the model. A short list of customer words the manual doesn't use ("onde vocês ficam?" -> endereço). |
| Guardrails | `guardrails.py`, `cart.py` | Input and output checks and the order rules. What each one really guarantees is in the next section. |
| Evidence | `tests/`, `evals/`, `observability.py`, `conversations/` | 255 tests without the API, 42 cases against the real model, one log line per answer, 5 example conversations. |

**Optional extension: MCP.** `mcp_server.py` exposes the same tools (data, manual, cart) to other
MCP clients. It is not required by the challenge. It exposes the tools only: a message that
comes through an MCP client does not pass through the chat's guardrails (input checks, price
check on the answer); that is up to the client. The cart rules inside `cart.py` still apply.

## What the prompt asks vs what the code guarantees

A rule written in the prompt is a request: the model follows it most of the time, not always.
So for each rule that matters I checked who really guarantees it, and how that is tested.

| Rule | Who guarantees it | Evidence |
|---|---|---|
| No invented prices | **Code, within limits.** Every value written as `R$` or "reais" must match a price field of a tool or a money value in the manual; ids, stock, days and the customer's numbers don't count. A customer's number only passes as a budget ("até mil reais") or in a sentence that refuses that value ("não posso confirmar R$ 1.500"), with no "mas" in between: "infelizmente custa R$ 1.500" is blocked. On a line that names a product, the value must be that product's; a product is named by the shortest part of its name no other product shares ("yamaha kit 1", "pearl kit"). Otherwise the answer is blocked and the model gets one more try. Limit: a price on a different line from the product name is only checked against all prices | `test_guardrails.py`, `test_agent.py`; evals `fake_discount`, `expired_promo` |
| Personal data | **Code.** CPF, card, e-mail and phone are masked before the model and the logs; customers' contacts are never loaded | `test_guardrails.py`, `test_tools.py`; eval `cpf_sent` |
| Someone else's order | **Code.** Order number + first name, with limits per conversation, per order and per server | `test_tools.py`; eval `order_someone_else` |
| No order without the customer | **Code** in the chat: the order is the exact version the customer was shown (any change asks for a new review), and confirming needs a new customer message after it, or the Confirmar button. **The model** decides if that message means yes. **Not guaranteed in MCP**: the server can't see the customer | `test_cart.py`, `test_mcp.py`; evals `no_order_without_ok`, `checkout` |
| The right products in the cart | **Code.** "Quero o 2" goes to `pick_from_catalog`, which takes the product from the numbers of the catalog on the screen, even one the code made and the model never saw. A product id from the model is only accepted if it was shown in a catalog, and nothing is added while answering a screen pick | `test_cart.py`; evals `pick_after_code_catalog`, `screen_pick`, `direct_add` |
| Complaints get the team and the 24 hours | **Code.** If the message is a complaint (broken, defect, "chateado"...) and the answer doesn't have both the full deadline ("24 horas úteis", not just a 24, which also appears in "PRS SE Custom 24") and a team contact, the code adds the contact and the deadline of the manual (7.3) | `test_agent.py`; eval `complaint` |
| Return window | **Code** for the data: for delivered orders the tool sends no date, and days are counted in Python from the date the customer gives. **Prompt** for asking that date, saying the 7 days are for online purchases, mentioning the 90-day warranty for defects and not deciding the return | `test_tools.py`; evals `return_received_date`, `return_no_order`, `defect_after_30_days` |
| The catalog the answer talks about | **Code.** If the answer says it put options in the catalog but none was made, the code builds it with the same filters (category, brand and budget from the model's search, or from the customer's words), so the customer sees what the text says and nothing outside what was asked | `test_agent.py`; evals `budget_list`, `budget_in_words`, `budget_and_brand` |
| Installments add up to the price | **Code.** Split in `Decimal`, rounded down, the last installment takes the cents left (11x of R$ 2.199,00 = 10x R$ 199,90 + R$ 200,00) | `test_tools.py`; eval `installments_last` |
| When the store opens | **Code.** Computed in Python with the store's time zone (UTC-4) and the manual's hours, and given to the model ready ("abre hoje às 09:00") | `test_prompt.py` |
| Instruments the store doesn't sell | **Code.** If the customer asks for one (surdo, pandeiro, reco-reco...) and the answer doesn't mention it, the code adds "Só não temos surdo no catálogo agora." | `test_agent.py`; eval `not_in_catalog` |
| Prompt injection | **Partly.** Patterns block the known phrasings; the layers above limit what a new one can do | `test_guardrails.py`; evals `injection`, `prompt_leak` |
| Tone, one question per message, no emojis, no product list in the text | **Prompt only.** The evaluation measures it; nothing in the code enforces it | eval checks on every answer of each conversation |

## Why each part is here (and what I left out)

The challenge asks for an agent that knows when to look at data and when at policies. Everything
beyond that had to earn its place by solving a problem I actually hit.

| Part | Asked for? | Why it is here | If I had to cut |
|---|---|---|---|
| Data tools (function calling) | Yes | Price, stock and order questions; values computed in Python | Can't cut |
| Manual search | Yes | The model decides when to read the policies, and the logs show which section it used | Could put the whole manual in the prompt (it is 8 pages); I'd lose that trace |
| Guardrails | Implied | A wrong price is what the manual most wants to avoid (7.1), plus LGPD | Keep |
| Evaluation | No | Unit tests can't tell how the model behaves. It found real bugs: missing discount percent, empty answer, a made-up product id | Keep |
| Logs | No | Found the retired model, the empty answer and the double add | Keep, it is small |
| Catalog and cart | No | Testing as a customer, a list of names in a chat was hard to choose from; the team gets a ready order | Second to cut. Isolated in `cart.py` |
| MCP server | No | The same tools in another app or agent without new code | First to cut. Nothing depends on it |
| Streamlit | Optional | Testing as a real customer would | The terminal alone is enough |

Left out on purpose: embeddings and a vector database (25 sections, keywords are enough and
testable), a SQL agent (closed functions can't build a wrong query), agent frameworks such as
LangChain (the SDK's function calling covers it), an API server (no channel to serve yet),
login (no real customer system to check against) and persisted history (one customer per run).

## Technical decisions

- **Hybrid agent: function calling for the data, retrieval for the manual.** The data questions
  are always the same few kinds, so small Python functions cover them and can't build a wrong
  query (no SQL agent). The manual search is one more tool, so the model decides *when* to read
  the policies, and that shows up in the logs.
- **Keyword retrieval, not embeddings.** The manual has 25 sections: keywords are enough, have no
  extra cost and always give the same result, which makes them easy to test.
- **Calculations in Python.** Discounts, PIX price, installments, days for the return window and
  cart totals are computed in the code. A wrong price is what the manual most wants to avoid (7.1),
  so the output guardrail blocks any value that didn't come from a price field of a tool.
- **Gemini Flash-Lite** (`google-genai`): cheap, fast, good Portuguese, function calling. A model
  list with fallback, because a model was retired for new keys during development. `MODELS`
  switches between Gemini models only: another provider would need a small adapter in `agent.py`.
- **Low variation:** temperature 0, fixed seed, minimal thinking, 800 output tokens.
- **Prompt:** instructions in English, answers in Portuguese. Persona from the manual (7.1), warm
  and short for a phone chat, one question per message, at most one recommendation taken from the
  product description, no list of products in the text when the catalog is on the screen. When
  rules compete, the prompt sets the order: answer correctly, policies, move forward, style.
- **Catalog and cart, separated from the screen.** `cart.py` has no UI code: the browser shows the
  catalog as buttons, the terminal and MCP as a numbered list ("quero o 2"). The catalog follows
  WhatsApp's multi-product message (sections by category, at most 30 products); with more than 10
  the customer first gets a summary by brand. The cart adds up across catalogs, the customer
  checks it before confirming, and changes after confirming warn the team right away.
- **Interface:** Streamlit, because the challenge is about the agent, not the frontend. The answer
  waits a moment that grows with its size, like someone typing. On errors the customer only sees
  a friendly message; the details go to the logs.
- **History:** in memory, one conversation per browser tab. Persisting it only makes sense with a
  real channel and customer ids.
- **Data treatment:** once at startup in `data.py`: active promotions only (biggest discount wins),
  accents ignored in search, status translated, and `customers.csv` loaded without phone and e-mail.

## Evaluation

Unit tests check the code; they can't tell how the model behaves. `evals/` runs 42 conversations
against the real model and checks **every answer of each conversation**, not only the last one:
technical errors, blocked prices, more than one question, more than 6 lines, prices without the
thousands separator, emojis, products or cart repeated in the text. The last answer is also
checked against what the case expects, looking at what the customer gets: tools called (and not
called), patterns instead of loose words (an opening hour has to be written as "9h" or "09:00",
not just contain a 9), a catalog that really reached the screen and only with what was asked
(brand, budget), the cart contents and that no
order was sent without confirmation. The 13 critical cases (fake discount, someone else's order,
injection, checkout, no order without an ok, screen pick, return window, budget questions,
brand and budget, an instrument the store doesn't sell, complaint, picking from a catalog the
model never saw) run 3 times.

Last run: 68 of 68 runs without problems ([report](evals/results/latest.md)); in 39 of the 42
cases the answers were identical across runs. The complaint case had failed twice (no 24 business
hours); that is now added by the code, not left to the prompt. Limits: the checks are rules,
not a judgment of tone or helpfulness; 42 cases is a small set; most cases run once, so a rare failure can hide.

The evaluation caught real problems: promotions without the discount percent, prices without the
thousands separator, the store's own e-mail being masked, products listed twice (text and
catalog), an empty answer after two tool calls, and the model adding a product with a made-up id
(a violão went in instead of a guitar). Manual tests and two outside reviews caught more: the cart
counting a screen pick twice, a cold tone, ids passing as prices, the return window using the
forecast date and only the last answer being checked. Each one became a test or an eval case.

## Assumptions

- **"Help the team":** the agent answers the recurring questions itself and hands to the team
  what needs a person: payment links, complaints, defects, anything it can't confirm.
- **Language:** code and docs in English (submission instructions), chat in Portuguese (the store,
  its customers and the manual are in Portuguese).
- **Order access:** order number + first name of the buyer (manual section 9). This is not
  authentication: it is fine for a prototype with fictional data, not for real customer data.
- **Return window:** the data only has the estimated delivery, not the day the customer got it.
  For delivered orders the tool doesn't send the forecast, so the agent asks the day they received
  it and the days are counted in Python (return by regret, 7 days); defects (30 days) count from
  the purchase date. The agent says the team confirms the return.
- **Installments:** the detailed rules in 3.1 (R$ 50 minimum up to 3x) win over the summary table.
  When the split is not exact, the last installment is a few cents different.
- **Contacts:** the manual has two phones and two spellings of the e-mail. The agent uses
  (67) 3341-4444, which appears twice, and the e-mail without accent.
- **Order handoff:** the challenge has no attendant system, so a confirmed order is written to
  `logs/team/orders.jsonl` (products and values only) for the team to read.

## Data issues found

- Some names don't match the description ("Yamaha Kit 1" is described as a Pearl Export).
- Product 136 (bass) costs R$ 441, far below the rest of the category; product 130 is
  `coming_soon` with stock; order 8 is still `shipped` with a February estimate.

## Limitations and next steps

- First name is not authentication, so this must not serve real customer data as it is. Next:
  a code sent to the customer's registered phone.
- In the MCP server, the server can't tell if the customer answered between `show_cart` and
  `confirm_order`; it marks `confirm_order` as destructive so the client asks the person.
- Injection detection is pattern-based; the other layers limit the damage (see SECURITY.md).
- Limits (order tries, messages per minute) live in memory and reset on restart. Next: Redis and
  a limit per IP.
- The handoff is a file, not a queue with acknowledgement. Next: a ticket in the store's system.
- Rules that only the prompt holds still slip sometimes. In `conversations/04_return_policy.md`
  the agent said the 7 days had passed before asking the receiving date, then asked and counted
  it right in the next message. I kept it as it came out instead of generating it again.
- Holidays are not in the data, so on a holiday the agent would say the store is open. Time zone:
  Campo Grande is UTC-4 all year (no daylight saving since 2019).
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
  a test for each attack. A second, independent audit (ChatGPT) found six more issues, like ids
  passing as prices and the return window using the forecast date; I reproduced and fixed them.
- Before each commit, I had the assistant replay the commit order in a copy of the repository and
  run the tests at every step.
