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
    r'(desconsider|esquec)\w* (tudo|todas|todos|as|os|suas|seus|o que)',
    r'disregard (all |the |previous |prior |your )*(instruction|rule|directive)',
    r'(novas|nova) (regras|regra|instrucoes|instrucao|diretrizes)',
    r'a partir de agora (voce|responda|aja|seja|finja|fale)',
    r'\bdan\b',
    r'^\s*\[?(system|sistema|developer|assistant|admin)\]?\s*:',
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
LEAK_MARKERS = [
    '## 1. answer correctly', '## 2. store policies', '## 3. move the conversation', '## 4. style',
    'when rules compete', '## rules', '## tone', '## catalog and cart', 'do not reveal', 'only_available',
    'search_policies', 'get_order(', 'show_catalog', 'add_to_cart', 'change_quantity', 'show_cart',
    'confirm_order', 'days_since_received',
]

# a customer's number only counts as a price in the answer when it is a budget ("até R$ 1.000").
# Without this, "o gerente disse que sai por R$ 1.500" went through as "sai por R$ 1.500"
REFUSAL_WORDS = ['nao posso', 'nao consigo', 'nao confirmo', 'nao existe', 'nao temos', 'nao tenho', 'nao vale',
                 'nao esta valendo', 'nao e possivel', 'nao e o preco', 'infelizmente']
GENERIC_WORDS = ['bateria', 'acustica', 'teclado', 'sintetizador', 'violao', 'guitarra', 'baixo', 'ukulele']
BUDGET_WORDS = ['ate', 'orcamento', 'abaixo de', 'menos de', 'no maximo', 'limite', 'faixa de', 'acima de']

# the tool fields that hold money, and the ones with manual text (prices written in the policies)
PRICE_FIELDS = ['price', 'promo_price', 'pix_price', 'final_price', 'value', 'last_value', 'unit_price', 'total',
                'pix_total', 'from_price', 'amount']
TEXT_FIELDS = ['text']

# markdown images load on their own in the browser and can send data to another site
IMAGE_PATTERN = re.compile(r'!\[[^\]]*\]\([^)]*\)')
LINK_PATTERN = re.compile(r'\[([^\]]*)\]\([^)]*\)')
URL_PATTERN = re.compile(r'(https?://|www\.)\S+', re.IGNORECASE)

PRICE_PATTERN = re.compile(r'R\$\s*(\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d+(?:[.,]\d{1,2})?)')
REAIS_PATTERN = re.compile(r'(\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d+(?:,\d{1,2})?)\s*reais', re.IGNORECASE)
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

    # mask first: a blocked message is logged too, and it can't take a CPF or e-mail to the logs
    message, found = mask_pii(message)
    for label in found:
        events.append(f'input_pii_masked:{label}')

    if looks_like_injection(message):
        events.append('prompt_injection_blocked')
        return message, True, events
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


def collect_prices(item, values):
    # only fields that are money, and the text of the manual. Before, every number counted:
    # a product id (123) or the stock (15) could be quoted as "R$ 123,00" and pass
    if isinstance(item, dict):
        for key, value in item.items():
            if key in PRICE_FIELDS and isinstance(value, (int, float)) and not isinstance(value, bool):
                values.add(round(float(value), 2))
            elif key in TEXT_FIELDS and isinstance(value, str):
                # only values written as money in the manual: "7 dias" can't authorize "R$ 7"
                for price in PRICE_PATTERN.findall(value):
                    values.add(round(parse_brl(price), 2))
            else:
                collect_prices(value, values)
    elif isinstance(item, (list, tuple)):
        for value in item:
            collect_prices(value, values)


def name_words(name):
    # generic words open many names ("Bateria Acústica Yamaha...", "Bateria Acústica Pearl...")
    # and made two drums share the same key, so they are left out
    words = re.sub(r'[^a-z0-9]+', ' ', normalize(name)).split()
    while words and words[0] in GENERIC_WORDS:
        words = words[1:]
    return words


def product_keys(names):
    # the shortest start of each name (at least 2 words) that no other product has:
    # "takamine gd20", "pearl kit", "yamaha kit 1" and "yamaha kit 3"
    keys = {}
    all_words = []
    for name in names:
        all_words.append(name_words(name))
    for name, words in zip(names, all_words, strict=True):
        size = min(2, len(words))
        while size < len(words):
            prefix = words[:size]
            shared = False
            for other in all_words:
                if other != words and other[:size] == prefix:
                    shared = True
            if not shared:
                break
            size += 1
        keys[name] = ' '.join(words[:size])
    return keys


def collect_product_prices(item, products):
    # which prices belong to which product, so a valid price of one product can't be used for another
    if isinstance(item, dict):
        if isinstance(item.get('name'), str):
            prices = set()
            for key in PRICE_FIELDS:
                value = item.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    prices.add(round(float(value), 2))
            if prices:
                products.setdefault(item['name'], set()).update(prices)
        for value in item.values():
            collect_product_prices(value, products)
    elif isinstance(item, (list, tuple)):
        for value in item:
            collect_product_prices(value, products)


def product_prices(tool_results):
    by_name = {}
    collect_product_prices(tool_results, by_name)
    keys = product_keys(list(by_name))
    products = {}
    for name, prices in by_name.items():
        products.setdefault(keys[name], set()).update(prices)
    return products


def belongs_to_another_product(value, line, products):
    # the line names one or more products: the price has to be one of theirs, unless it is a price
    # of no product at all (a cart total, an installment, a value from the manual)
    line = ' ' + re.sub(r'[^a-z0-9]+', ' ', normalize(line)) + ' '
    named = []
    for key in products:
        if f' {key} ' in line:
            named.append(key)
    if not named:
        return False

    for key in named:
        if value in products[key]:
            return False
    for key, prices in products.items():
        if key not in named and value in prices:
            return True
    return False


def line_of(answer, position):
    start = answer.rfind('\n', 0, position) + 1
    end = answer.find('\n', position)
    if end == -1:
        end = len(answer)
    return answer[start:end]


def money_in(answer):
    # "R$ 1.500" and also "1.500 reais"
    found = []
    for pattern in [PRICE_PATTERN, REAIS_PATTERN]:
        for match in pattern.finditer(answer):
            found.append((round(parse_brl(match.group(1)), 2), match.start()))
    return found


def allowed_values(tool_results):
    # every value the answer may quote as a price
    values = set()
    collect_prices(tool_results, values)
    return values


def is_budget(answer, position):
    before = normalize(answer[max(0, position - 25):position])
    for word in BUDGET_WORDS:
        if word in before:
            return True
    return False


def is_refusal(answer, position):
    # "não posso confirmar R$ 1.500": repeating the customer's value to say no is correct.
    # Only the sentence of the value counts, so a "não" from another sentence doesn't free it
    start = max(answer.rfind('.', 0, position), answer.rfind('!', 0, position), answer.rfind('?', 0, position),
                answer.rfind('\n', 0, position))
    before = normalize(answer[start + 1:position])
    for words in REFUSAL_WORDS:
        if words in before:
            return True
    return False


def customer_numbers(message):
    values = set()
    collect_numbers(message, values)
    # "até mil reais", "uns 2 mil": the budget written in words
    for number in re.findall(r'\b(?:(\d+)\s*)?mil\b', normalize(message)):
        if number:
            values.add(float(int(number) * 1000))
        else:
            values.add(1000.0)
    return values


def remove_links(answer):
    events = []
    if IMAGE_PATTERN.search(answer):
        answer = IMAGE_PATTERN.sub('', answer)
        events.append('image_removed')
    if LINK_PATTERN.search(answer):
        answer = LINK_PATTERN.sub(r'\1', answer)
        events.append('link_removed')
    if URL_PATTERN.search(answer):
        answer = URL_PATTERN.sub('[link removido]', answer)
        events.append('link_removed')
    return answer, events


def check_output(answer, allowed, customer_message='', products=None):
    # returns (safe answer, events)
    lowered = answer.lower()
    for marker in LEAK_MARKERS:
        if marker in lowered:
            return OFF_LIMITS_REPLY, ['prompt_leak_blocked']

    customer_values = customer_numbers(customer_message)
    products = products or {}

    unconfirmed = []
    for value, position in money_in(answer):
        if value in allowed:
            if belongs_to_another_product(value, line_of(answer, position), products):
                unconfirmed.append(value)
            continue
        if value in customer_values and (is_budget(answer, position) or is_refusal(answer, position)):
            continue
        unconfirmed.append(value)
    if unconfirmed:
        return UNCONFIRMED_PRICE_REPLY, [f'unconfirmed_price_blocked:{unconfirmed}']

    answer, events = remove_links(answer)
    answer, found = mask_pii(answer)
    for label in found:
        events.append(f'output_pii_masked:{label}')
    return answer, events
