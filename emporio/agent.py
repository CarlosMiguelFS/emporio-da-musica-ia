import os
import time

from google import genai
from google.genai import types

from emporio import guardrails
from emporio.cart import Cart, brand_in, budget_in, cart_tools, categories_in, items_not_sold
from emporio.observability import describe_error, log_turn, safe_args
from emporio.prompt import build_prompt
from emporio.settings import STORE_EMAIL, STORE_PHONE
from emporio.text_utils import normalize
from emporio.tools import TOOLS, conversation_tools

DEFAULT_MODELS = 'gemini-3.1-flash-lite,gemini-3.5-flash-lite'  # cheapest first
MAX_TOOL_CALLS = 5
MAX_OUTPUT_TOKENS = 800
SEED = 42

# overloaded, rate limited or retired model: try the next one (401/402/403 are account problems)
FALLBACK_CODES = [404, 429, 500, 502, 503, 504]

# what the customer sees when something breaks, the real reason goes to logs/
ERROR_REPLY = f'Tive um problema técnico agora. Tenta de novo em instantes ou fala com a equipe no {STORE_PHONE}.'

# sent to the model (never shown to the customer) when it quotes a price that no tool returned
PRICE_RETRY_NOTE = (
    '[internal check, not from the customer] Your last answer had prices that did not come from '
    "a tool. Call search_products now and answer the customer's last message again using only "
    'the values the tool returns.'
)

# the model once called the tools and sent no text
EMPTY_ANSWER_NOTE = (
    "[internal check, not from the customer] Your last answer had no text. Answer the customer's "
    'last message now, using the tool results you already have.'
)
EMPTY_REPLY = 'Desculpa, me enrolei aqui. Pode mandar sua pergunta de novo?'

# words of a complaint about a product that arrived broken or with a defect (manual 7.3)
COMPLAINT_WORDS = ['rachad', 'quebrad', 'trincad', 'amassad', 'arranhad', 'defeito', 'danificad', 'avaria',
                   'estragad', 'nao funciona', 'veio errado', 'chegou errado', 'reclama', 'chateado', 'insatisfeit']
COMPLAINT_NOTE = (
    f'Para registrar e resolver, fala com a nossa equipe no WhatsApp {STORE_PHONE} ou no e-mail '
    f'{STORE_EMAIL}: eles retornam em até 24 horas úteis.'
)

# counts the whole server: a new tab is a new conversation, so the 30 messages aren't enough
MAX_MESSAGES_PER_MINUTE = 60
BUSY_REPLY = f'Estou com muitas conversas agora. Tenta de novo em um minutinho ou fala com a equipe no {STORE_PHONE}.'
recent_messages = []

_client = None


def get_client():
    # one client for the whole run, otherwise the chat loses its connection
    global _client
    if _client is None:
        _client = genai.Client()
    return _client


def get_models():
    models = []
    for name in os.getenv('MODELS', DEFAULT_MODELS).split(','):
        name = name.strip()
        if name:
            models.append(name)
    return models


def build_config(model, tools=None):
    # Gemini 2.5 turns thinking off with budget 0; Gemini 3 uses levels and "minimal" is the lowest
    if model.startswith('gemini-2.5'):
        thinking = types.ThinkingConfig(thinking_budget=0)
    else:
        thinking = types.ThinkingConfig(thinking_level='MINIMAL')

    if tools is None:
        tools = TOOLS

    return types.GenerateContentConfig(
        system_instruction=build_prompt(),
        tools=tools,
        # temperature 0 + fixed seed + minimal thinking = the answer varies as little as possible
        temperature=0,
        seed=SEED,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        thinking_config=thinking,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(maximum_remote_calls=MAX_TOOL_CALLS),
    )


class Conversation:
    # Gemini chat that moves to the next model in MODELS when one is down, with the history

    def __init__(self, models):
        self.models = models
        self.index = 0
        self.model = models[0]
        self.fallbacks = []
        self.cart = Cart()
        self.tools = conversation_tools() + cart_tools(self.cart)
        self.chat = self.start_chat(history=None)

    def start_chat(self, history):
        config = build_config(self.model, self.tools)
        return get_client().chats.create(model=self.model, config=config, history=history)

    def send_message(self, message):
        self.fallbacks = []
        while True:
            try:
                return self.chat.send_message(message)
            except Exception as error:
                code = None
                if hasattr(error, 'code'):
                    code = error.code
                is_last_model = self.index + 1 >= len(self.models)
                if code not in FALLBACK_CODES or is_last_model:
                    raise

                next_model = self.models[self.index + 1]
                self.fallbacks.append({'from': self.model, 'to': next_model, 'code': code})

                history = self.chat.get_history()
                self.index += 1
                self.model = next_model
                self.chat = self.start_chat(history)

    def get_history(self):
        return self.chat.get_history()


def new_chat():
    if not os.getenv('GEMINI_API_KEY'):
        raise SystemExit('GEMINI_API_KEY is empty. Put your key in the .env file.')
    return Conversation(get_models())


def last_turn_parts(history):
    # the SDK doesn't return the tool calls in chats, so they are read from the history
    parts = []
    for content in history:
        for part in content.parts or []:
            if content.role == 'user' and part.text:
                parts = []  # the history comes whole, keep only the last turn
            parts.append(part)
    return parts


def all_tool_results(history):
    results = []
    for content in history:
        for part in content.parts or []:
            if part.function_response:
                results.append(part.function_response.response)
    return results


def format_call(call):
    args = []
    for name, value in safe_args(call.args).items():
        args.append(f'{name}={value!r}')
    return f'{call.name}({", ".join(args)})'


def ask(chat, text, customer_message):
    # checked against every tool result of the chat, so an earlier price can be repeated
    response = chat.send_message(text)
    history = chat.get_history()

    calls = []
    for part in last_turn_parts(history):
        if part.function_call:
            calls.append(part.function_call)

    results = all_tool_results(history)
    allowed = guardrails.allowed_values(results)
    products = guardrails.product_prices(results)
    answer, events = guardrails.check_output(response.text or '', allowed, customer_message, products)
    return response, calls, answer, events


def show_promised_catalog(chat, calls, answer, message=''):
    # the answer talks about a catalog but none was made: make it from the search or the customer words
    cart = getattr(chat, 'cart', None)
    if not cart or 'catalogo' not in normalize(answer):
        return False

    for call in calls:
        if call.name == 'show_catalog':
            return False

    # same filters as the normal path: category, brand and budget from the search, or from the customer
    for call in calls:
        if call.name != 'search_products':
            continue
        args = call.args or {}
        term = args.get('term') or ''
        category = args.get('category') or ', '.join(categories_in(term + ' ' + message))
        brand = brand_in(term) or brand_in(message)
        max_price = args.get('max_price') or budget_in(message)
        result = cart.catalog(category, '', brand, max_price)
        if result.get('total'):
            return True

    categories = categories_in(message)
    if categories:
        result = cart.catalog(', '.join(categories), '', brand_in(message), budget_in(message))
        return bool(result.get('total'))
    return False


def say_what_is_missing(answer, message):
    # "uma guitarra, uma bateria e um surdo": the answer has to say there is no surdo
    missing = []
    for item in items_not_sold(message):
        if item.replace('-', ' ') not in normalize(answer).replace('-', ' '):
            missing.append(item)
    if not missing:
        return answer
    return f"{answer}\n\nSó não temos {' nem '.join(missing)} no catálogo agora."


def is_complaint(message):
    words = normalize(message)
    for word in COMPLAINT_WORDS:
        if word in words:
            return True
    return False


def say_who_handles_complaints(answer, message):
    # manual 7.3: a complaint always gets the team contact and the 24 business hours
    if not is_complaint(message) or '24' in answer:
        return answer
    return f'{answer}\n\n{COMPLAINT_NOTE}'


def price_was_blocked(events):
    for event in events:
        if event.startswith('unconfirmed_price_blocked'):
            return True
    return False


def elapsed_ms(start):
    return round((time.perf_counter() - start) * 1000)


def server_is_busy():
    global recent_messages
    now = time.monotonic()
    kept = []
    for moment in recent_messages:
        if now - moment < 60:
            kept.append(moment)
    recent_messages = kept

    if len(recent_messages) >= MAX_MESSAGES_PER_MINUTE:
        return True
    recent_messages.append(now)
    return False


def reply(chat, session_id, message):
    # guardrails -> model + tools -> guardrails -> log. Never raises
    start = time.perf_counter()
    record = {'session': session_id}
    events = []

    # the cart needs to know a new customer message started (see Cart.confirm)
    cart = getattr(chat, 'cart', None)
    if cart:
        cart.new_turn()

    try:
        message, blocked, events = guardrails.check_input(message)
        record['message'] = message
        if blocked:
            record['answer'] = guardrails.OFF_LIMITS_REPLY
            record['guardrails'] = events
            log_turn(record)
            return guardrails.OFF_LIMITS_REPLY, []

        if server_is_busy():
            record['answer'] = BUSY_REPLY
            record['guardrails'] = events + ['server_busy']
            log_turn(record)
            return BUSY_REPLY, []

        response, calls, answer, output_events = ask(chat, message, message)

        if price_was_blocked(output_events):
            # second chance: the model answered prices from memory, ask it to check the system
            response, retry_calls, answer, retry_events = ask(chat, PRICE_RETRY_NOTE, message)
            calls = calls + retry_calls
            output_events = output_events + ['price_retry'] + retry_events

        if not answer.strip():
            response, retry_calls, answer, retry_events = ask(chat, EMPTY_ANSWER_NOTE, message)
            calls = calls + retry_calls
            output_events = output_events + ['empty_answer_retry'] + retry_events
            if not answer.strip():
                answer = EMPTY_REPLY

        if show_promised_catalog(chat, calls, answer, message):
            output_events.append('catalog_added_by_code')

        checked = say_what_is_missing(answer, message)
        if checked != answer:
            answer = checked
            output_events.append('missing_item_added_by_code')

        checked = say_who_handles_complaints(answer, message)
        if checked != answer:
            answer = checked
            output_events.append('complaint_note_added_by_code')

    except Exception as error:
        record['model'] = chat.model
        record['fallbacks'] = chat.fallbacks
        record['answer'] = ERROR_REPLY
        record['guardrails'] = events
        record['error'] = describe_error(error)
        record['latency_ms'] = elapsed_ms(start)
        log_turn(record)
        return ERROR_REPLY, []

    tools_used = []
    for call in calls:
        tools_used.append({'name': call.name, 'args': safe_args(call.args)})

    tokens = {'prompt': None, 'output': None, 'total': None}
    usage = response.usage_metadata
    if usage:
        tokens = {
            'prompt': usage.prompt_token_count,
            'output': usage.candidates_token_count,
            'total': usage.total_token_count,
        }

    record['model'] = chat.model  # after a fallback it's not the first model anymore
    record['fallbacks'] = chat.fallbacks
    record['answer'] = answer
    record['tools'] = tools_used
    record['guardrails'] = events + output_events
    record['tokens'] = tokens
    record['latency_ms'] = elapsed_ms(start)
    log_turn(record)

    calls_text = []
    for call in calls:
        calls_text.append(format_call(call))
    return answer, calls_text
