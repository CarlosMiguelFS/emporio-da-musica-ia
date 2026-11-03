import os
import time

from google import genai
from google.genai import types

from emporio import guardrails
from emporio.cart import Cart, cart_tools
from emporio.observability import describe_error, log_turn, safe_args
from emporio.prompt import build_prompt
from emporio.settings import STORE_PHONE
from emporio.tools import TOOLS, conversation_tools

DEFAULT_MODELS = 'gemini-3.1-flash-lite,gemini-3.5-flash-lite'  # cheapest first
MAX_TOOL_CALLS = 5
MAX_OUTPUT_TOKENS = 800
SEED = 42

# overloaded, rate limited or retired model: worth trying the next one.
# 401/402/403 are account problems, switching model doesn't help.
FALLBACK_CODES = [404, 429, 500, 502, 503, 504]

# the only thing the customer sees when something breaks. The real reason goes to logs/ only:
# error details on screen tell an attacker what is behind the agent.
ERROR_REPLY = f'Tive um problema técnico agora. Tenta de novo em instantes ou fala com a equipe no {STORE_PHONE}.'

# sent to the model (never shown to the customer) when it quotes a price that no tool returned
PRICE_RETRY_NOTE = (
    '[internal check, not from the customer] Your last answer had prices that did not come from '
    "a tool. Call search_products now and answer the customer's last message again using only "
    'the values the tool returns.'
)

# the model once called the tools and sent no text at all
EMPTY_ANSWER_NOTE = (
    "[internal check, not from the customer] Your last answer had no text. Answer the customer's "
    'last message now, using the tool results you already have.'
)
EMPTY_REPLY = 'Desculpa, me enrolei aqui. Pode mandar sua pergunta de novo?'

# the 30 messages limit is per conversation, and a new tab is a new conversation.
# This one counts the whole server, so nobody runs up the API bill opening tabs
MAX_MESSAGES_PER_MINUTE = 60
BUSY_REPLY = f'Estou com muitas conversas agora. Tenta de novo em um minutinho ou fala com a equipe no {STORE_PHONE}.'
recent_messages = []

_client = None


def get_client():
    # one client for the whole run: if it gets garbage collected it closes the connection the chat uses
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
    # Gemini chat that jumps to the next model in MODELS when the current one is down.
    # The history goes along, so the customer doesn't notice the switch.

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
    # the tool calls are read from the chat history: in google-genai 2.x the response's
    # automatic_function_calling_history comes empty in chats, and the price guardrail
    # was blocking correct prices because it couldn't see the tool results
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
    # prices are checked against every tool result in the conversation,
    # so a price confirmed two messages ago can still be repeated
    response = chat.send_message(text)
    history = chat.get_history()

    calls = []
    for part in last_turn_parts(history):
        if part.function_call:
            calls.append(part.function_call)

    allowed = guardrails.allowed_values(all_tool_results(history))
    answer, events = guardrails.check_output(response.text or '', allowed, customer_message)
    return response, calls, answer, events


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
    # one customer message: guardrails -> model + tools -> guardrails -> log.
    # Never raises: any failure becomes ERROR_REPLY plus a line in the log.
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
