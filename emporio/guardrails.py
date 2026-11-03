import re
import unicodedata

from emporio.settings import STORE_EMAIL, STORE_PHONE, STORE_WHATSAPP
from emporio.text_utils import normalize

MAX_MESSAGE_LENGTH = 1000

STORE_CONTACTS = [
    STORE_PHONE,
    STORE_WHATSAPP,
    STORE_EMAIL,
    'contato@empóriodamusica.com.br',
]

OFF_LIMITS_REPLY = (
    'Consigo te ajudar só com assuntos da Empório da Música: instrumentos, preços, pedidos, '
    'pagamento, trocas e entregas. Em que posso ajudar?'
)
UNCONFIRMED_PRICE_REPLY = (
    'Desculpa, não consegui confirmar esse valor no sistema agora. Pode perguntar de novo? '
    f'Se preferir, fala com a equipe no {STORE_PHONE}.'
)

# checked on the normalized text (no accents, lowercase)
INJECTION_PATTERNS = [
    r'ignor\w* (todas |as |os |suas |tudo )*(instruc|regra|orientac)',
    r'ignore (all |the |previous |your )*(instruction|rule)',
    r'(system|developer) (prompt|message|mode)',
    r'prompt (do|de) sistema',
    r'(suas|your) (instrucoes|instructions|regras|rules)',
    r'(voce agora e|you are now|aja como|act as|finja (que|ser)|pretend)',
    r'modo (desenvolvedor|dev|deus)',
    r'jailbreak',
]

# the order matters: card numbers before CPF and phone
PII_PATTERNS = [
    ('[cartão removido]', r'\b(?:\d[ -]?){13,19}\b'),
    ('[CPF removido]', r'\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b'),
    # the domain can't end with ".", otherwise the period that ends a sentence becomes part of
    # the e-mail and the store's own address stops matching STORE_CONTACTS
    ('[e-mail removido]', r'[\w.+-]+@[\w-]+(?:\.[\w-]+)+'),
    ('[telefone removido]', r'\(?\b\d{2}\)?\s?9?\d{4}-?\d{4}\b'),
]

# pieces of the system prompt that should never show up in an answer
LEAK_MARKERS = ['## rules', '## tone', 'do not reveal', 'only_available', 'search_policies', 'get_order(']

PRICE_PATTERN = re.compile(r'R\$\s*(\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d+(?:[.,]\d{1,2})?)')
NUMBER_PATTERN = re.compile(r'\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d+(?:[.,]\d{1,2})?')


def mask_pii(text):
    found = []
    for label, pattern in PII_PATTERNS:
        for value in re.findall(pattern, text):
            if value in STORE_CONTACTS:
                continue
            text = text.replace(value, label)
            found.append(label)
    return text, found


def remove_control_chars(text):
    clean = ''
    for char in text:
        if char == '\n' or not unicodedata.category(char).startswith('C'):
            clean += char
    return clean


def looks_like_injection(message):
    text = normalize(message)
    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, text):
            return True
    return False


def check_input(message):
    # returns (clean message, blocked, events)
    events = []
    message = remove_control_chars(message).strip()

    if len(message) > MAX_MESSAGE_LENGTH:
        message = message[:MAX_MESSAGE_LENGTH]
        events.append('input_truncated')

    if looks_like_injection(message):
        events.append('prompt_injection_blocked')
        return message, True, events

    message, found = mask_pii(message)
    for label in found:
        events.append(f'input_pii_masked:{label}')
    return message, False, events


def parse_brl(text):
    # '2.199,00' / '2199,00' / '2199.0' / '2.000'
    if ',' in text:
        return float(text.replace('.', '').replace(',', '.'))
    if re.fullmatch(r'\d{1,3}(\.\d{3})+', text):
        return float(text.replace('.', ''))
    return float(text)


def collect_numbers(item, values):
    # goes through dicts, lists and texts picking every number it finds
    if isinstance(item, bool):
        return
    if isinstance(item, (int, float)):
        values.add(round(float(item), 2))
    elif isinstance(item, str):
        for number in NUMBER_PATTERN.findall(item):
            values.add(round(parse_brl(number), 2))
    elif isinstance(item, dict):
        for value in item.values():
            collect_numbers(value, values)
    elif isinstance(item, (list, tuple)):
        for value in item:
            collect_numbers(value, values)


def allowed_values(tool_results, customer_message):
    # every number the answer may quote as a price: what came from the tools
    # (data and manual) and what the customer wrote
    values = set()
    collect_numbers(tool_results, values)
    collect_numbers(customer_message, values)
    return values


def check_output(answer, allowed):
    # returns (safe answer, events)
    lowered = answer.lower()
    for marker in LEAK_MARKERS:
        if marker in lowered:
            return OFF_LIMITS_REPLY, ['prompt_leak_blocked']

    unconfirmed = []
    for price in PRICE_PATTERN.findall(answer):
        value = round(parse_brl(price), 2)
        if value not in allowed:
            unconfirmed.append(value)
    if unconfirmed:
        return UNCONFIRMED_PRICE_REPLY, [f'unconfirmed_price_blocked:{unconfirmed}']

    answer, found = mask_pii(answer)
    events = []
    for label in found:
        events.append(f'output_pii_masked:{label}')
    return answer, events
