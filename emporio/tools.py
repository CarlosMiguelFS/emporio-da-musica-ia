import json
import time
from datetime import date
from decimal import ROUND_DOWN, Decimal

import pandas as pd

from emporio.data import CUSTOMERS, ORDER_ITEMS, ORDERS, PRODUCTS
from emporio.rag import search_policies
from emporio.settings import STORE_PHONE
from emporio.text_utils import normalize

# The functions the model can call are the ones in TOOLS (end of the file). Only they have
# docstrings and type hints: the Gemini SDK reads them to explain each tool to the model.

PIX_DISCOUNT = 0.05
CENT = Decimal('0.01')
MAX_RESULTS = 10
MAX_TEXT_ARG = 100
MAX_AMOUNT = 1_000_000

# order lookup limits. A right answer doesn't reset them: before, someone could fail twice,
# look up their own order and start again, forever
MAX_ORDER_ATTEMPTS = 3  # wrong tries in one conversation
MAX_TRIES_PER_ORDER = 3  # wrong tries on the same order number, from any conversation
MAX_FAILS_ON_SERVER = 20  # wrong tries in the whole server
LOCK_SECONDS = 15 * 60

ORDER_STATUS = {
    'pending': 'aguardando confirmação do pagamento',
    'confirmed': 'pagamento confirmado, pedido em separação',
    'shipped': 'enviado',
    'delivered': 'entregue',
    'cancelled': 'cancelado',
}

PRODUCT_STATUS = {
    'active': 'ativo',
    'discontinued': 'descontinuado',
    'coming_soon': 'em breve (ainda não está à venda)',
}

PAYMENT = {'pix': 'PIX', 'debit': 'cartão de débito', 'boleto': 'boleto'}

# same message for "doesn't exist" and "wrong name", so nobody can find out which order numbers exist
ORDER_NOT_FOUND = 'Não encontrei um pedido com esse número e esse nome.'
ORDER_LOCKED = f'Muitas tentativas sem sucesso. Por segurança, fale com a equipe no {STORE_PHONE}.'
RECEIVED_DATE_NOTE = (
    'O dia em que o cliente recebeu não está nos dados. Não diga quando foi entregue. '
    'Para devolução por arrependimento, pergunte e use days_since_received.'
)

order_fails = {}
server_fails = []


def new_attempts():
    return {'failed': 0}


# for whoever calls get_order without a conversation of its own (MCP server, tests)
shared_attempts = new_attempts()


def now():
    return time.monotonic()


def recent(times):
    kept = []
    for moment in times:
        if now() - moment < LOCK_SECONDS:
            kept.append(moment)
    return kept


def order_is_locked(order_id, attempts):
    if attempts['failed'] >= MAX_ORDER_ATTEMPTS:
        return True
    if len(recent(order_fails.get(order_id, []))) >= MAX_TRIES_PER_ORDER:
        return True
    return len(recent(server_fails)) >= MAX_FAILS_ON_SERVER


def register_fail(order_id, attempts, order_exists):
    global server_fails
    attempts['failed'] += 1
    server_fails = recent(server_fails) + [now()]
    # only real orders are kept, otherwise trying random numbers would fill the memory
    if order_exists:
        order_fails[order_id] = recent(order_fails.get(order_id, [])) + [now()]


def clean_text(value):
    if value is None:
        return ''
    return str(value)[:MAX_TEXT_ARG]


def clean_amount(value):
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return 0.0

    if amount < 0:
        return 0.0
    if amount > MAX_AMOUNT:
        return MAX_AMOUNT
    return amount


def product_summary(row):
    specs = {}
    if isinstance(row['specs'], str):
        specs = json.loads(row['specs'])

    product = {
        'id': int(row['product_id']),
        'name': row['name'],
        'category': row['category'],
        'price': float(row['price_brl']),
        'stock': int(row['stock_quantity']),
        'status': PRODUCT_STATUS.get(row['status'], row['status']),
        'description': row['description'],
        'specs': specs,
    }

    if row['discount'] > 0:
        product['promotion'] = row['promotion']
        product['discount_percent'] = int(row['discount'])
        product['promo_price'] = float(row['final_price'])
    else:
        # PIX discount does not stack with promotions (manual 6.2)
        product['pix_price'] = round(float(row['price_brl']) * (1 - PIX_DISCOUNT), 2)
    return product


def payment_text(code):
    if code.startswith('credit_'):
        installments = code.replace('credit_', '')
        return f'cartão de crédito em {installments}'
    return PAYMENT.get(code, code)


def days_since(iso_date):
    return (date.today() - date.fromisoformat(iso_date)).days


def search_products(term: str, category: str, max_price: float, only_available: bool) -> dict:
    """Searches the store catalog. Use it for any question about price, stock, promotions
    or product options.

    Args:
        term: words from the name, brand or model (e.g. "takamine gd20", "yamaha"). Use "" for no filter.
        category: one of these categories, or "" for all: Guitarras, Baixos, Baterias e Percussão,
            Teclados e Pianos, Violões, Instrumentos de Sopro (Madeiras), Instrumentos de Sopro (Metais),
            Cordas Orquestrais, Ukuleles.
        max_price: maximum price in BRL, promotions already applied. Use 0 for no limit.
        only_available: True returns only active products with stock. Use False when the customer
            asks about a specific model, so you can tell them if it is sold out or discontinued.
    """
    term = clean_text(term)
    category = clean_text(category)
    max_price = clean_amount(max_price)

    result = PRODUCTS

    # every word has to be somewhere in name + category + description
    for word in normalize(term).split():
        result = result[result['search'].str.contains(word, regex=False)]

    if category:
        categories = result['category'].map(normalize)
        result = result[categories.str.contains(normalize(category), regex=False)]

    if max_price > 0:
        result = result[result['final_price'] <= max_price]

    if only_available:
        result = result[result['status'] == 'active']
        result = result[result['stock_quantity'] > 0]

    result = result.sort_values('final_price')

    products = []
    for _, row in result.head(MAX_RESULTS).iterrows():
        products.append(product_summary(row))

    return {'total_found': len(result), 'products': products}


def active_promotions() -> dict:
    """Lists the products with an active promotion, with original price, discount and promo price."""
    on_sale = PRODUCTS[PRODUCTS['discount'] > 0]
    on_sale = on_sale.sort_values('discount', ascending=False)

    products = []
    for _, row in on_sale.iterrows():
        products.append(product_summary(row))
    return {'products': products}


GET_ORDER_DOC = """Looks up status, items, tracking and dates of an order. Ask the customer for the order
    number and the name of the person who placed it before calling.

    Args:
        order_id: order number.
        customer_name: name given by the customer (first name is enough).
    """


def get_order(order_id: int, customer_name: str) -> dict:
    return find_order(order_id, customer_name, shared_attempts)


get_order.__doc__ = GET_ORDER_DOC


def order_tool(attempts):
    # each conversation counts its own wrong tries, one customer can't lock the others
    def get_order(order_id: int, customer_name: str) -> dict:
        return find_order(order_id, customer_name, attempts)

    get_order.__doc__ = GET_ORDER_DOC
    return get_order


def find_order(order_id, customer_name, attempts):
    try:
        order_id = int(order_id)
    except (TypeError, ValueError):
        order_id = 0

    if order_is_locked(order_id, attempts):
        return {'error': ORDER_LOCKED}

    orders = ORDERS[ORDERS['order_id'] == order_id]
    if orders.empty:
        register_fail(order_id, attempts, order_exists=False)
        return {'error': ORDER_NOT_FOUND}
    order = orders.iloc[0]

    customer = CUSTOMERS[CUSTOMERS['customer_id'] == order['customer_id']].iloc[0]

    # LGPD: only shows the order if the first name matches who bought it
    first_name = normalize(customer['name']).split()[0]
    given_names = normalize(clean_text(customer_name)).split()
    if first_name not in given_names:
        register_fail(order_id, attempts, order_exists=True)
        return {'error': ORDER_NOT_FOUND}

    items = []
    order_items = ORDER_ITEMS[ORDER_ITEMS['order_id'] == order_id].merge(PRODUCTS, on='product_id')
    for _, item in order_items.iterrows():
        items.append({'product': item['name'], 'quantity': int(item['quantity'])})

    answer = {
        'order_id': order_id,
        'customer': customer['name'].split()[0],
        'status': ORDER_STATUS.get(order['status'], order['status']),
        'order_date': order['order_date'],
        'days_since_order': days_since(order['order_date']),
        'total': float(order['total_brl']),
        'payment': payment_text(order['payment_method']),
        'items': items,
    }

    if pd.notna(order['tracking_code']):
        answer['tracking_code'] = order['tracking_code']

    # the forecast only helps while the order is on the way. Once delivered, the model kept using it
    # as the day the customer got it ("entregue em fevereiro, o prazo já passou"), so it is left out
    # and the real day is asked to the customer (days_since_received)
    if pd.notna(order['estimated_delivery']) and order['status'] != 'delivered':
        answer['estimated_delivery'] = order['estimated_delivery']
        answer['estimated_delivery_note'] = 'previsão de entrega, não é a data em que o cliente recebeu'
    if order['status'] == 'delivered':
        answer['received_date_note'] = RECEIVED_DATE_NOTE

    if pd.notna(order['notes']):
        answer['notes'] = order['notes']

    return answer


def min_installment(installments):
    if installments <= 3:
        return 50
    if installments <= 6:
        return 80
    return 100


def installment_options(amount: float) -> dict:
    """Returns the interest-free credit card installment options for an amount, following
    the store's minimum installment rules.

    Args:
        amount: purchase amount in BRL.
    """
    amount = clean_amount(amount)
    if amount <= 0:
        return {'error': 'Valor inválido.'}

    # in Decimal and rounded down: the last installment takes the cents left, so the installments
    # always add up to the price (11x of R$ 199,91 used to give R$ 2.199,01 for R$ 2.199,00)
    total = Decimal(str(amount)).quantize(CENT)
    options = [{'installments': 1, 'value': float(total)}]
    for installments in range(2, 13):
        value = (total / installments).quantize(CENT, rounding=ROUND_DOWN)
        if value < min_installment(installments):
            continue
        option = {'installments': installments, 'value': float(value)}
        last = total - value * (installments - 1)
        if last != value:
            option['last_value'] = float(last)
        options.append(option)

    return {
        'amount': amount,
        'interest_free_options': options,
        'can_combine_payment_methods': amount > 2000,
    }


def days_since_received(received_date: str) -> dict:
    """Counts the days since the customer received the product, for the 7-day return window
    (manual 4.1). Ask the customer the day they received it; never use the estimated delivery.

    Args:
        received_date: the day the customer says they received it, as YYYY-MM-DD.
    """
    try:
        received = date.fromisoformat(clean_text(received_date)[:10])
    except ValueError:
        return {'error': 'Data inválida, use o formato AAAA-MM-DD.'}

    days = (date.today() - received).days
    if days < 0:
        return {'error': 'Essa data ainda não chegou.'}
    return {'received_date': received.isoformat(), 'days_since_received': days, 'within_7_days': days <= 7}


TOOLS = [search_products, active_promotions, get_order, installment_options, search_policies, days_since_received]


def conversation_tools():
    # same tools, but get_order counts the wrong tries of this conversation only
    attempts = new_attempts()
    tools = []
    for tool in TOOLS:
        if tool is get_order:
            tools.append(order_tool(attempts))
        else:
            tools.append(tool)
    return tools
