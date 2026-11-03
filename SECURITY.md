# Security and LGPD

No system that talks to an LLM is unbreakable. Prompt injection in particular can't be
fully solved with filters. The approach here is **layers**: even if one fails, the next one
limits the damage. The most important layer is the simplest: the model only has read-only
access to the data it needs, so there is not much it can do even when tricked.

## Threats and what handles each one

| Threat | Layer | Where |
|---|---|---|
| Prompt injection ("ignore your instructions", "desconsidere as orientações", "SYSTEM: ...") | Pattern check before the model; blocked messages never reach it. Tested with the bypasses found in a review (synonyms, English, fake "SYSTEM:" lines) | `guardrails.check_input` |
| Injection hidden in data (e.g. a product description) | Prompt rule: tool results are data, not instructions | `prompt.py` |
| System prompt leak | Prompt rule + output check for prompt fragments | `prompt.py`, `guardrails.check_output` |
| Invented price or discount | Every value written as `R$` or "reais" must come from a price field of a tool (price, promo, PIX, cart total, installment) or a money value in the manual; ids, stock, quantities and days don't count. On a line that names a product, the value must be that product's: a product is named by the shortest part of its name no other product shares. A value the customer wrote only passes as a budget ("até R$ 1.000", "até mil reais") or in a sentence that refuses that value ("não posso confirmar R$ 1.500") with no "mas" in between; "infelizmente custa R$ 1.500" is blocked | `guardrails.check_output` |
| Seeing someone else's order | Order number + first name of the owner | `tools.get_order` |
| Finding out which order numbers exist | Same error message for "not found" and "wrong name" | `tools.get_order` |
| Brute force on orders | 3 wrong tries per conversation, 3 per order number (from any conversation or tab) and 20 in the whole server, for 15 minutes. A right answer doesn't reset the count (it used to: fail twice, look up your own order, start again) | `tools.find_order` |
| Leaking customer contact data | Phone and e-mail are never loaded (only `customer_id` and `name`); only the first name leaves the tool | `data.py`, `tools.get_order` |
| Customer sends CPF / card / e-mail / phone | Masked before anything else, so even a blocked message reaches the logs masked. Conversations saved with `--save` are masked too and go to `logs/conversations/` (out of git, deleted after 30 days), never to the examples folder | `guardrails.check_input`, `main.save` |
| Model writes someone's personal data | Output masking (store contacts are kept) | `guardrails.check_output` |
| Huge messages, control characters | Truncated to 1000 chars, control chars removed | `guardrails.check_input` |
| Bad tool arguments (types, sizes, negative values) | Validated and clamped in every tool; search uses `regex=False` (no regex injection); no SQL, no `eval`, no file paths from input | `tools.py` |
| Model stuck calling tools in a loop | Max 5 tool calls per message | `agent.py` |
| Cost abuse | Max 30 messages per conversation, 60 messages per minute in the whole server (a new tab doesn't get around it), max 800 output tokens, short product data in the catalog | `app.py`, `main.py`, `agent.py`, `cart.py` |
| Error details on screen (information disclosure: URLs, model, provider messages, stack traces) | The customer only ever sees one friendly message. `reply` never raises; library logs and warnings are silenced; `main` has a last catch-all; `--debug` shows tool names only, never errors. The real reason goes to `logs/` | `agent.reply`, `main.silence_internal_output` |
| API key leak | Only in `.env` (git-ignored); never logged; error logs keep only the error type | `.gitignore`, `agent.reply` |
| Web UI: error details, exposure, cross-customer data | `showErrorDetails = "none"` (no stack trace on the page), listens on localhost only, telemetry off, developer menu hidden; one conversation per browser tab (`session_state`); same `reply` pipeline as the CLI. The "typing" dots are the only HTML on the page: fixed CSS written in the code, no JavaScript; text from the model or the customer is never rendered as HTML (tested) | `.streamlit/config.toml`, `app.py`, `emporio/chat_ui.py`, `tests/test_app.py` |
| MCP server exposure | stdio only (no network port). It exposes the tools only: messages from an MCP client do not pass through the chat guardrails (input checks, price check on the answer), that is up to the client. Inside the tools: store tools are read-only, cart tools need a valid `session_id`, keep at most 500 carts, keep the cart rules of `cart.py`, and `confirm_order` is marked destructive so MCP clients ask the person | `mcp_server.py` |
| Order sent without the customer seeing it (e.g. after an injection) | The order is the exact version the customer was shown: any change after `show_cart` asks for a new review. Confirming needs a customer message after the cart was shown, or the Confirmar button. The code can't tell if that message means yes; the model judges it | `cart.confirm`, `cart.cancel_confirmation` |
| Flooding the team with fake orders or changes | At most 5 versions per order; the team is warned once per change, not on every click; quantities limited to the stock | `cart.py` |
| Cart counted twice or a wrong product added (screen pick + model) | While answering a screen pick the model can't add anything; and it can only add products that were shown in a catalog of the conversation, so a made-up id is refused (it happened in a test: a violão went in instead of a guitar) | `cart.add_from_model` |
| Data sent to another site through the answer (markdown image) | Images are removed from the answer, links keep only their text, raw URLs are removed | `guardrails.remove_links` |
| Vulnerable dependency update | Exact versions pinned | `requirements.txt` |

## LGPD (Lei 13.709/2018) and the store manual (section 9)

| Principle | How it is applied |
|---|---|
| Purpose (art. 6, I) | Personal data is only used to answer about the customer's own order |
| Necessity / minimization (art. 6, III) | The model never receives phone, e-mail or full name; the agent never asks for CPF, card or address; if the customer sends them, they are masked |
| Transparency (art. 6, VI and art. 9) | Notice at the start of the chat saying the conversation is logged, which data is not kept (CPF, card, e-mail, phone) and for how long. It does not promise "no personal data": a name typed in the chat stays in the log |
| Security (art. 6, VII and art. 46) | All the layers in the table above |
| Storage limitation (art. 15 and 16) | Logs are deleted after 30 days (`observability.purge_old_logs`), logs stay out of git |
| Data subject rights (art. 18) | Asked to delete data, the agent explains the policy and sends the customer to the team, as the manual says |
| No sharing with third parties | Only the minimum goes to the LLM provider: masked message and tool results without contact data |

What the logs keep: session id, masked message, answer, tool names and arguments (with
`customer_name` replaced by `***`), guardrail events, tokens, latency and error type.

## Known gaps

- Injection detection is pattern-based; a creative attacker can still get past it. The other
  layers limit what they can do: read-only data tools, prices checked in code, no order without
  a customer message in between, no personal contact data anywhere near the model.
- First name is not real authentication. In production: login, or a code sent to the
  customer's phone.
- The limits (order tries, messages per minute, carts) live in memory: a restart clears them.
  A real service would keep them in a shared store (e.g. Redis) and also limit per IP.
- In the MCP server the client chooses the `session_id`. That's fine for a local stdio server
  started by the client itself; over a network the session would have to come from authentication.
- In the MCP server each tool call counts as a new step, so the server can't prove the customer
  answered between `show_cart` and `confirm_order`. `confirm_order` is marked destructive, so
  MCP clients ask the person before running it; the real check depends on the client.
- The order handoff is a file (`logs/team/orders.jsonl`), not a real queue with acknowledgement.
  If the file can't be written, the order is not marked as sent and the customer is told to try
  again; a failing log never breaks the answer.
- The message still goes to Google's API (with personal data masked). In production this needs
  a data processing agreement with the provider.
