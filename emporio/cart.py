import json
import uuid
from datetime import datetime
from decimal import Decimal
from difflib import get_close_matches

from emporio import observability
from emporio.data import PRODUCTS
from emporio.settings import STORE_PHONE
from emporio.text_utils import normalize
from emporio.tools import product_summary

CATEGORIES = [
    'Guitarras', 'Baixos', 'Baterias e Percussão', 'Teclados e Pianos', 'Violões',
    'Instrumentos de Sopro (Madeiras)', 'Instrumentos de Sopro (Metais)', 'Cordas Orquestrais', 'Ukuleles',
]

# Kalani before Kala, otherwise every Kalani becomes Kala
BRANDS = ['Music Man', 'Fender', 'Gibson', 'Ibanez', 'PRS', 'Yamaha', 'Pearl', 'Korg', 'Nord', 'Roland',
          'Kalani', 'Kala', 'Ohana', 'Shelby', 'Tagima', 'Giannini', 'Rozini', 'Crafter', 'Martin',
          'Taylor', 'Takamine']

MAX_CATALOG = 30  # WhatsApp catalog limit
MAX_LIST = 10  # more than this, send a summary by brand
MAX_QUANTITY = 99
MAX_DESCRIPTION = 90

MAX_VERSIONS = 5  # changes after that go to a person, so nobody floods the team
TEAM_DIR = 'team'
TEAM_FILE_NAME = 'orders.jsonl'
EDITING_NOTE = 'Cliente está alterando o pedido. Não gere o link de pagamento ainda.'
SEND_FAILED = f'Não consegui enviar o pedido para a equipe agora. Peça para tentar de novo ou ligar no {STORE_PHONE}.'
TOO_MANY_CHANGES =f'Esse pedido já foi alterado muitas vezes. Peça para o cliente falar com a equipe no {STORE_PHONE}.'


def brl(value):
    text = f'{value:,.2f}'
    text = text.replace(',', 'X').replace('.', ',').replace('X', '.')
    return f'R$ {text}'


def to_money(value):
    return Decimal(str(value)).quantize(Decimal('0.01'))


def sale_price(product):
    if 'promo_price' in product:
        return to_money(product['promo_price'])
    return to_money(product['price'])


def pix_price(product):
    # PIX doesn't stack with a promotion (manual 6.2)
    if 'pix_price' in product:
        return to_money(product['pix_price'])
    return sale_price(product)


def brand_of(product):
    for brand in BRANDS:
        if brand.lower() in product['name'].lower():
            return brand
    return 'Outras marcas'


def find_category(name):
    names = {}
    for category in CATEGORIES:
        names[normalize(category)] = category

    match = get_close_matches(normalize(name).strip(), names.keys(), n=1, cutoff=0.6)
    if match:
        return names[match[0]]

    for key, category in names.items():
        if normalize(name).strip() and normalize(name).strip() in key:
            return category
    return None


def available(category):
    result = PRODUCTS[PRODUCTS['category'] == category]
    result = result[result['status'] == 'active']
    result = result[result['stock_quantity'] > 0]

    products = []
    for _, row in result.iterrows():
        products.append(product_summary(row))
    return products


def find_product(product_id):
    try:
        product_id = int(product_id)
    except (TypeError, ValueError):
        return None

    rows = PRODUCTS[PRODUCTS['product_id'] == product_id]
    if rows.empty:
        return None
    return product_summary(rows.iloc[0])


def clean_quantity(value):
    try:
        quantity = int(value)
    except (TypeError, ValueError):
        return 0
    if quantity < 0:
        return 0
    if quantity > MAX_QUANTITY:
        return MAX_QUANTITY
    return quantity


def model_view(product, number=None):
    # short description to save tokens
    item = {
        'product_id': product['id'],
        'name': product['name'],
        'brand': brand_of(product),
        'stock': product['stock'],
        'description': product['description'][:MAX_DESCRIPTION],
    }
    if number:
        item['number'] = number
    if 'promo_price' in product:
        item['price'] = product['price']
        item['promo_price'] = product['promo_price']
        item['discount_percent'] = product['discount_percent']
    else:
        item['price'] = product['price']
        item['pix_price'] = product['pix_price']
    return item


class Cart:
    def __init__(self, session_id=''):
        self.session_id = session_id
        self.items = {}
        self.numbers = {}
        self.waiting_confirmation = False
        self.order = None
        self.events = []
        # picked on the screen, so the model doesn't add it again
        self.picked_on_screen = {}
        self.turn = 0
        self.shown_on_turn = None
        # products shown in a catalog of this conversation, the only ones the model can add
        self.seen = set()

    def catalog(self, categories, term='', brand='', max_price=0):
        found = []
        for name in str(categories).split(','):
            category = find_category(name)
            if category and category not in found:
                found.append(category)

        if not found:
            return {'error': 'Categoria não encontrada.', 'categories': CATEGORIES}

        term_words = normalize(term).split()
        brand = str(brand or '').strip()
        try:
            max_price = float(max_price or 0)
        except (TypeError, ValueError):
            max_price = 0

        sections = []
        for category in found:
            products = []
            for product in available(category):
                if brand and normalize(brand) not in normalize(product['name']):
                    continue
                if max_price > 0 and sale_price(product) > to_money(max_price):
                    continue
                products.append(product)
            sections.append({'category': category, 'products': products})

        # the model the customer asked for comes first
        if term_words:
            wanted = []
            for section in sections:
                for product in list(section['products']):
                    name = normalize(product['name'])
                    has_all = True
                    for word in term_words:
                        if word not in name:
                            has_all = False
                    if has_all:
                        wanted.append(product)
                        section['products'].remove(product)
            if wanted:
                sections.insert(0, {'category': 'O que você pediu', 'products': wanted})

        self.numbers = {}
        count = 0
        shown = []
        for section in sections:
            by_brand = {}
            for product in section['products']:
                by_brand.setdefault(brand_of(product), []).append(product)

            brands = []
            for name in sorted(by_brand):
                products = sorted(by_brand[name], key=sale_price)
                items = []
                for product in products:
                    if count >= MAX_CATALOG:
                        break
                    count += 1
                    self.numbers[count] = product['id']
                    self.seen.add(product['id'])
                    items.append({'number': count, 'product': product})
                brands.append({'brand': name, 'items': items, 'models': len(products),
                               'from_price': sale_price(products[0])})
            shown.append({'category': section['category'], 'total': len(section['products']), 'brands': brands})

        all_products = 0
        for section in shown:
            all_products += section['total']

        summary = all_products > MAX_LIST and not brand and not max_price and not term_words

        catalog = {'sections': shown, 'total': count, 'all_products': all_products, 'summary': summary}
        self.events.append({'kind': 'catalog', 'catalog': catalog})

        if count == 0:
            return {'shown_to_customer': False, 'total': 0, 'note': 'Nada disponível com esse filtro.'}

        if summary:
            return {'shown_to_customer': True, 'mode': 'summary', 'total': all_products,
                    'brands': self.brand_summary(shown)}

        products = []
        for section in shown:
            for group in section['brands']:
                for item in group['items']:
                    products.append(model_view(item['product'], item['number']))
        return {'shown_to_customer': True, 'mode': 'list', 'total': count, 'products': products}

    def brand_summary(self, sections):
        summary = []
        for section in sections:
            for group in section['brands']:
                summary.append({
                    'category': section['category'],
                    'brand': group['brand'],
                    'models': group['models'],
                    'from_price': float(group['from_price']),
                })
        return summary

    def add(self, product_id, quantity=1):
        product = find_product(product_id)
        if not product or product['status'] != 'ativo' or product['stock'] <= 0:
            return {'error': 'Produto não está disponível para venda.'}

        quantity = clean_quantity(quantity)
        if quantity == 0:
            return {'error': 'Quantidade inválida.'}

        # same product adds up, doesn't replace
        new_quantity = self.items.get(product['id'], 0) + quantity
        limited = new_quantity > product['stock']
        if limited:
            new_quantity = product['stock']
        self.items[product['id']] = new_quantity

        self.start_editing()
        result = self.summary()
        if limited:
            result['note'] = f"Só temos {product['stock']} unidades de {product['name']} em estoque."
        return result

    def add_from_screen(self, product_id, quantity):
        result = self.add(product_id, quantity)
        if 'error' not in result:
            self.picked_on_screen[int(product_id)] = quantity
        return result

    def add_from_model(self, product_id, quantity):
        # the customer picked on the screen in this message: everything is already in the cart.
        # The model once "added it again" with a made-up id and a wrong product went in
        if self.picked_on_screen:
            result = self.summary()
            result['note'] = 'O cliente já colocou os produtos pela tela. Nada foi adicionado.'
            return result

        product = find_product(product_id)
        if not product or product['id'] not in self.seen:
            return {'error': 'Esse produto não apareceu em nenhum catálogo desta conversa. '
                             'Chame show_catalog com o nome do produto em term e use o product_id que voltar.'}
        return self.add(product_id, quantity)

    def set_quantity(self, product_id, quantity):
        product = find_product(product_id)
        if not product or product['id'] not in self.items:
            return {'error': 'Esse produto não está no carrinho.'}

        quantity = clean_quantity(quantity)
        if quantity == 0:
            del self.items[product['id']]
        else:
            self.items[product['id']] = min(quantity, product['stock'])

        self.start_editing()
        return self.summary()

    def change_by(self, product_id, step):
        if product_id not in self.items:
            return self.summary()
        return self.set_quantity(product_id, self.items[product_id] + step)

    def lines(self):
        lines = []
        for product_id, quantity in self.items.items():
            product = find_product(product_id)
            lines.append({'product': product, 'quantity': quantity, 'value': sale_price(product) * quantity})
        return lines

    def total(self):
        total = Decimal('0')
        for line in self.lines():
            total += line['value']
        return total

    def pix_total(self):
        total = Decimal('0')
        for line in self.lines():
            total += pix_price(line['product']) * line['quantity']
        return total

    def summary(self):
        items = []
        for line in self.lines():
            items.append({
                'product_id': line['product']['id'],
                'name': line['product']['name'],
                'quantity': line['quantity'],
                'unit_price': float(sale_price(line['product'])),
                'value': float(line['value']),
            })

        result = {'items': items, 'total': float(self.total()), 'pix_total': float(self.pix_total())}
        if self.order:
            result['order'] = {'number': self.order['number'], 'version': self.order['version'],
                               'status': self.order['status']}
        return result

    def new_turn(self):
        self.turn += 1

    def ask_confirmation(self):
        if not self.items:
            return {'error': 'O carrinho está vazio.'}
        self.waiting_confirmation = True
        self.shown_on_turn = self.turn
        self.events.append({'kind': 'cart'})
        result = self.summary()
        result['shown_to_customer'] = True
        return result

    def start_editing(self):
        # warn the team before they create the payment link
        if self.order and self.order['status'] == 'enviado para a equipe':
            self.order['status'] = 'em alteração'
            # if this warning fails to save, the new confirmation still sends what changed
            self.tell_team(self.order['number'], self.order['version'], 'editing', EDITING_NOTE)

    def snapshot(self):
        items = {}
        for line in self.lines():
            items[line['product']['id']] = [line['product']['name'], line['quantity']]
        return items

    def confirm(self, by_customer=False):
        # by_customer is the Confirmar button. The model only confirms if the cart was shown in an
        # earlier message: without this, an injected message could make it call show_cart and
        # confirm_order together and send an order nobody saw
        if not self.items:
            return {'error': 'O carrinho está vazio.'}

        # the button already confirmed, don't send it twice
        if self.order and self.order['status'] == 'enviado para a equipe':
            self.waiting_confirmation = False
            return {'status': 'already_sent', 'order_number': self.order['number'],
                    'version': self.order['version'], 'total': float(self.total())}

        if not self.waiting_confirmation:
            return {'error': 'Mostre o carrinho com show_cart e espere o cliente confirmar.'}
        if not by_customer and self.shown_on_turn == self.turn:
            return {'error': 'O cliente ainda não viu o carrinho. Espere ele responder.'}
        if self.order and self.order['version'] >= MAX_VERSIONS:
            return {'error': TOO_MANY_CHANGES}

        # the order only counts as sent after the team file is written. If writing fails,
        # nothing changes and the customer can try again
        if not self.order:
            # random, so two orders in the same second don't get the same number
            number = uuid.uuid4().hex[:6].upper()
            if not self.tell_team(number, 1, 'new', 'Pedido novo, pode gerar o link de pagamento.'):
                return {'error': SEND_FAILED}
            self.order = {'number': number, 'version': 1, 'items': self.snapshot(), 'status': 'enviado para a equipe'}
            self.waiting_confirmation = False
            return {'status': 'sent', 'order_number': number, 'version': 1, 'total': float(self.total())}

        changes = describe_changes(self.order['items'], self.snapshot())
        version = self.order['version'] + 1
        note = f"Pedido alterado: {'; '.join(changes) or 'sem mudanças'}. Pode gerar o link."
        if not self.tell_team(self.order['number'], version, 'changed', note):
            return {'error': SEND_FAILED}
        self.order['version'] = version
        self.order['items'] = self.snapshot()
        self.order['status'] = 'enviado para a equipe'
        self.waiting_confirmation = False
        return {'status': 'updated', 'order_number': self.order['number'], 'version': version,
                'changes': changes, 'total': float(self.total())}

    def tell_team(self, number, version, event, note):
        # no personal data, only products and values. Returns False if the file couldn't be written
        items = []
        for line in self.lines():
            items.append({'product': line['product']['name'], 'quantity': line['quantity']})

        record = {
            'time': datetime.now().isoformat(timespec='seconds'),
            'session': self.session_id,
            'order': number,
            'version': version,
            'event': event,
            'note': note,
            'items': items,
            'total': float(self.total()),
        }
        # own folder, so the logs summary doesn't read the orders as conversation turns
        team_dir = observability.LOG_DIR / TEAM_DIR
        try:
            team_dir.mkdir(parents=True, exist_ok=True)
            with open(team_dir / TEAM_FILE_NAME, 'a', encoding='utf-8') as file:
                file.write(json.dumps(record, ensure_ascii=False) + '\n')
        except OSError:
            return False
        return True

    def take_events(self):
        events = self.events
        self.events = []
        return events


def describe_changes(old, new):
    changes = []
    for product_id, (name, quantity) in old.items():
        if product_id not in new:
            changes.append(f'tirou {name}')
        elif new[product_id][1] != quantity:
            changes.append(f'{name}: {quantity} -> {new[product_id][1]}')
    for product_id, (name, quantity) in new.items():
        if product_id not in old:
            changes.append(f'colocou {name} x{quantity}')
    return changes


# text for channels without buttons (terminal, MCP)
def price_text(product):
    if 'promo_price' in product:
        return f"de {brl(product['price'])} por {brl(product['promo_price'])} ({product['discount_percent']}% off)"
    return f"{brl(product['price'])} ({brl(product['pix_price'])} no PIX)"


def catalog_text(catalog):
    if catalog['total'] == 0:
        return ''

    lines = []
    if catalog['summary']:
        for section in catalog['sections']:
            lines.append(section['category'])
            for group in section['brands']:
                models = 'modelo' if group['models'] == 1 else 'modelos'
                lines.append(f"  {group['brand']}: {group['models']} {models}, a partir de {brl(group['from_price'])}")
        return '\n'.join(lines)

    for section in catalog['sections']:
        if not section['brands']:
            continue
        lines.append(f"{section['category']} ({section['total']})")
        for group in section['brands']:
            if not group['items']:
                continue
            lines.append(f"  {group['brand']}")
            for item in group['items']:
                lines.append(f"    {item['number']}. {item['product']['name']}: {price_text(item['product'])}")
    return '\n'.join(lines)


def cart_text(cart):
    lines = []
    for line in cart.lines():
        lines.append(f"  {line['product']['name']} x{line['quantity']}: {brl(line['value'])}")
    lines.append(f'  Total: {brl(cart.total())} (no PIX: {brl(cart.pix_total())})')
    return '\n'.join(lines)


def cart_tools(cart):
    # each conversation has its own cart, so the tools are made for it

    def show_catalog(categories: str, term: str, brand: str, max_price: float) -> dict:
        """Shows the customer a catalog they can choose from. Use it whenever the customer wants
        to see options or buy one or more kinds of instrument. The app shows the products to the
        customer, so do not list them in your answer.

        Args:
            categories: one or more categories separated by commas, e.g. "Guitarras, Baterias e Percussão".
            term: model or brand the customer named, to show it first (e.g. "telecaster"). "" for none.
            brand: show only this brand, e.g. "Yamaha". "" for all.
            max_price: maximum price in BRL. 0 for no limit.
        """
        return cart.catalog(categories, term, brand, max_price)

    def add_to_cart(product_id: int, quantity: int) -> dict:
        """Adds a product to the cart. If it is already there the quantity adds up. When the
        customer picks by the catalog number ("quero o 2"), use the product_id of that number.

        Args:
            product_id: product_id from show_catalog or search_products.
            quantity: how many units to add.
        """
        return cart.add_from_model(product_id, quantity)

    def change_quantity(product_id: int, quantity: int) -> dict:
        """Sets the quantity of a product that is already in the cart. 0 removes it.

        Args:
            product_id: product_id of the item in the cart.
            quantity: the new quantity.
        """
        return cart.set_quantity(product_id, quantity)

    def show_cart() -> dict:
        """Shows the cart to the customer so they can check it before the order goes to the team.
        Use it when the customer says they are done, or wants to change the order."""
        return cart.ask_confirmation()

    def confirm_order() -> dict:
        """Sends the order to a store attendant, who creates the payment link. Only call it after
        show_cart and after the customer clearly said the cart is right."""
        return cart.confirm()

    return [show_catalog, add_to_cart, change_quantity, show_cart, confirm_order]
