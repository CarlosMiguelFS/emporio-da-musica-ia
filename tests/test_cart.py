import json
from decimal import Decimal

import pytest

from emporio import observability
from emporio.cart import MAX_CATALOG, MAX_VERSIONS, Cart, cart_text, cart_tools, catalog_text, find_category

TELECASTER = 134
LES_PAUL = 131  # only 2 in stock
YAMAHA_C40 = 81
TAYLOR_PROMO = 94  # on promotion, so no PIX discount on top
IBANEZ_DISCONTINUED = 136  # discontinued but still has stock
KALA_COMING_SOON = 130  # "em breve" but has stock


@pytest.fixture(autouse=True)
def temp_logs(tmp_path, monkeypatch):
    monkeypatch.setattr(observability, 'LOG_DIR', tmp_path)
    return tmp_path


def team_lines(temp_logs):
    file = temp_logs / 'team' / 'orders.jsonl'
    if not file.exists():
        return []
    lines = []
    for line in file.read_text(encoding='utf-8').splitlines():
        lines.append(json.loads(line))
    return lines


def all_numbers(cart):
    return sorted(cart.numbers)


def show_and_confirm(cart):
    # the cart is shown in one message and confirmed in the next one, like a real chat
    cart.ask_confirmation()
    cart.new_turn()
    return cart.confirm()


def confirmed_cart():
    cart = Cart('s1')
    cart.add(TELECASTER, 2)
    show_and_confirm(cart)
    return cart


# ---------- catalog ----------

@pytest.mark.parametrize('name, category', [
    ('violao', 'Violões'),
    ('Violões', 'Violões'),
    ('guitarra', 'Guitarras'),
    ('bateria', 'Baterias e Percussão'),
    ('percussão', 'Baterias e Percussão'),
    ('ukulele', 'Ukuleles'),
    ('teclado', 'Teclados e Pianos'),
])
def test_category_is_found_with_or_without_accent(name, category):
    assert find_category(name) == category


def test_unknown_category_returns_an_error():
    result = Cart().catalog('reco-reco', '', '', 0)
    assert 'error' in result
    assert 'Baterias e Percussão' in result['categories']


def test_many_products_give_a_summary_counting_every_brand():
    result = Cart().catalog('violão', '', '', 0)

    assert result['mode'] == 'summary'
    assert result['total'] == 33
    brands = {}
    for item in result['brands']:
        brands[item['brand']] = item
    # Yamaha is the last brand: with the limit of 30 it would lose models if counted wrong
    assert brands['Yamaha']['models'] == 7
    assert brands['Yamaha']['from_price'] == 599.9


def test_catalog_never_has_more_than_30_numbers():
    cart = Cart()
    cart.catalog('violão', '', '', 0)
    assert all_numbers(cart) == list(range(1, MAX_CATALOG + 1))


def test_brand_filter_gives_a_numbered_list():
    cart = Cart()
    result = cart.catalog('violão', '', 'Yamaha', 0)

    assert result['mode'] == 'list'
    assert result['total'] == 7
    for product in result['products']:
        assert product['brand'] == 'Yamaha'


def test_kalani_is_not_mixed_with_kala():
    result = Cart().catalog('ukulele', '', 'Kalani', 0)
    names = []
    for product in result['products']:
        names.append(product['name'])
    assert len(names) == 3
    for name in names:
        assert name.startswith('Kalani')


def test_brands_in_alphabetical_order_and_cheapest_first():
    cart = Cart()
    cart.catalog('Guitarras, Baterias e Percussão', '', '', 0)
    catalog = cart.take_events()[0]['catalog']

    for section in catalog['sections']:
        names = []
        for group in section['brands']:
            names.append(group['brand'])
            prices = []
            for item in group['items']:
                prices.append(item['product']['price'])
            assert prices == sorted(prices)
        assert names == sorted(names)


def test_model_the_customer_named_comes_first():
    result = Cart().catalog('guitarra', 'telecaster', '', 0)
    first = result['products'][0]
    assert first['number'] == 1
    assert first['product_id'] == TELECASTER


def test_max_price_filter_uses_the_promotion_price():
    # Crafter HT-100 costs 2.399 but is 1.967,18 on promotion
    result = Cart().catalog('violão', '', 'Crafter', 2000)
    ids = []
    for product in result['products']:
        ids.append(product['product_id'])
    assert 90 in ids


def test_unavailable_products_never_show_up():
    cart = Cart()
    cart.catalog('Baixos, Ukuleles', '', '', 0)
    assert IBANEZ_DISCONTINUED not in cart.numbers.values()
    assert KALA_COMING_SOON not in cart.numbers.values()


def test_filter_without_results():
    result = Cart().catalog('guitarra', '', '', 100)
    assert result['total'] == 0
    assert result['shown_to_customer'] is False


def test_catalog_for_the_screen_goes_out_once():
    cart = Cart()
    cart.catalog('guitarra', '', '', 0)
    assert len(cart.take_events()) == 1
    assert cart.take_events() == []


# ---------- cart ----------

def test_same_product_adds_up_instead_of_replacing():
    cart = Cart()
    cart.add(TELECASTER, 1)
    cart.add(YAMAHA_C40, 1)
    cart.add(TELECASTER, 2)
    assert cart.items == {TELECASTER: 3, YAMAHA_C40: 1}


def test_quantity_is_limited_to_the_stock_and_the_model_is_told():
    cart = Cart()
    result = cart.add(LES_PAUL, 5)
    assert cart.items[LES_PAUL] == 2
    assert 'Só temos 2' in result['note']


@pytest.mark.parametrize('product_id', [IBANEZ_DISCONTINUED, KALA_COMING_SOON, 999999, 'abc'])
def test_products_that_cant_be_sold_are_refused(product_id):
    cart = Cart()
    assert 'error' in cart.add(product_id, 1)
    assert cart.items == {}


@pytest.mark.parametrize('quantity', [0, -3, 'dois', None])
def test_invalid_quantity_is_refused(quantity):
    cart = Cart()
    assert 'error' in cart.add(TELECASTER, quantity)


def test_quantity_zero_removes_the_item():
    cart = Cart()
    cart.add(TELECASTER, 2)
    cart.set_quantity(TELECASTER, 0)
    assert cart.items == {}


def test_minus_button_down_to_zero_removes_the_item():
    cart = Cart()
    cart.add(YAMAHA_C40, 1)
    cart.change_by(YAMAHA_C40, -1)
    assert cart.items == {}


def test_changing_an_item_that_is_not_in_the_cart():
    assert 'error' in Cart().set_quantity(TELECASTER, 1)


def test_totals_in_decimal_and_pix_does_not_stack_with_promotion():
    cart = Cart()
    cart.add(YAMAHA_C40, 3)  # 599,90 / PIX 569,90
    cart.add(TAYLOR_PROMO, 1)  # promotion 5.519,08, PIX is the same

    assert cart.total() == Decimal('1799.70') + Decimal('5519.08')
    assert cart.pix_total() == Decimal('1709.70') + Decimal('5519.08')
    assert cart.summary()['total'] == 7318.78


# ---------- order ----------

def test_order_needs_the_cart_shown_first():
    cart = Cart()
    cart.add(TELECASTER, 1)
    assert 'error' in cart.confirm()
    assert cart.order is None


def test_empty_cart_cant_be_confirmed():
    cart = Cart()
    assert 'error' in cart.ask_confirmation()
    assert 'error' in cart.confirm()


def test_confirmed_order_goes_to_the_team(temp_logs):
    cart = confirmed_cart()

    assert cart.order['status'] == 'enviado para a equipe'
    lines = team_lines(temp_logs)
    assert len(lines) == 1
    assert lines[0]['event'] == 'new'
    assert lines[0]['items'] == [{'product': 'Fender Telecaster Player Series', 'quantity': 2}]
    assert lines[0]['total'] == 10800.0


def test_confirming_again_without_changes_does_not_send_twice(temp_logs):
    cart = confirmed_cart()
    result = cart.confirm()
    assert result['status'] == 'already_sent'
    assert len(team_lines(temp_logs)) == 1


def test_changing_after_confirming_warns_the_team_right_away(temp_logs):
    cart = confirmed_cart()
    cart.set_quantity(TELECASTER, 1)

    assert cart.order['status'] == 'em alteração'
    lines = team_lines(temp_logs)
    assert lines[-1]['event'] == 'editing'
    assert 'Não gere o link' in lines[-1]['note']

    # a second change doesn't repeat the warning
    cart.add(YAMAHA_C40, 1)
    assert len(team_lines(temp_logs)) == 2


def test_new_confirmation_sends_version_2_with_what_changed(temp_logs):
    cart = confirmed_cart()
    cart.set_quantity(TELECASTER, 1)
    cart.add(YAMAHA_C40, 1)
    result = show_and_confirm(cart)

    assert result['status'] == 'updated'
    assert result['version'] == 2
    assert 'Fender Telecaster Player Series: 2 -> 1' in result['changes']
    assert 'colocou Yamaha C40 Nylon Natural x1' in result['changes']
    assert team_lines(temp_logs)[-1]['event'] == 'changed'


def test_order_file_has_no_personal_data(temp_logs):
    confirmed_cart()
    line = team_lines(temp_logs)[0]
    assert sorted(line) == ['event', 'items', 'note', 'order', 'session', 'time', 'total', 'version']


# ---------- text for channels without buttons ----------

def test_summary_text_shows_brands_not_products():
    cart = Cart()
    cart.catalog('violão', '', '', 0)
    text = catalog_text(cart.take_events()[0]['catalog'])
    assert 'Yamaha: 7 modelos, a partir de R$ 599,90' in text
    assert 'Yamaha C40' not in text


def test_list_text_is_numbered_with_pix_price():
    cart = Cart()
    cart.catalog('violão', '', 'Yamaha', 0)
    text = catalog_text(cart.take_events()[0]['catalog'])
    assert '1. Yamaha C40 Nylon Natural: R$ 599,90 (R$ 569,90 no PIX)' in text


def test_promotion_text_shows_the_discount():
    cart = Cart()
    cart.catalog('ukulele', '', 'Kalani', 0)
    text = catalog_text(cart.take_events()[0]['catalog'])
    assert 'de R$ 359,00 por R$ 323,10 (10% off)' in text


def test_cart_text_has_total_and_pix():
    cart = Cart()
    cart.add(YAMAHA_C40, 2)
    assert 'Total: R$ 1.199,80 (no PIX: R$ 1.139,80)' in cart_text(cart)


# ---------- tools ----------

def test_tools_change_only_their_own_cart():
    first = Cart()
    second = Cart()
    tools = {}
    for tool in cart_tools(first):
        tools[tool.__name__] = tool

    tools['show_catalog']('violão', '', 'Yamaha', 0)
    tools['add_to_cart'](YAMAHA_C40, 1)
    assert first.items == {YAMAHA_C40: 1}
    assert second.items == {}


def test_tools_have_docstrings_for_the_model():
    for tool in cart_tools(Cart()):
        assert tool.__doc__


def test_model_does_not_add_again_what_the_screen_already_added():
    # real bug: the screen added 2, the model was told and called add_to_cart(2) again: 4 in the cart
    cart = Cart()
    tools = {}
    for tool in cart_tools(cart):
        tools[tool.__name__] = tool

    cart.add_from_screen(TELECASTER, 2)
    result = tools['add_to_cart'](TELECASTER, 2)

    assert cart.items == {TELECASTER: 2}
    assert 'Nada foi adicionado' in result['note']


def test_model_adds_nothing_while_answering_a_screen_pick():
    # real bug: answering "Adicionei ao carrinho: Telecaster x1", the model called add_to_cart
    # with a made-up id and a violão went into the cart
    cart = Cart()
    tools = {}
    for tool in cart_tools(cart):
        tools[tool.__name__] = tool
    cart.catalog('violão', '', 'Yamaha', 0)
    cart.catalog('guitarra', '', '', 0)

    cart.add_from_screen(TELECASTER, 1)
    tools['add_to_cart'](YAMAHA_C40, 1)
    assert cart.items == {TELECASTER: 1}

    cart.picked_on_screen = {}
    tools['add_to_cart'](TELECASTER, 1)
    assert cart.items[TELECASTER] == 2


def test_model_cant_add_a_product_it_never_showed():
    cart = Cart()
    tools = {}
    for tool in cart_tools(cart):
        tools[tool.__name__] = tool

    result = tools['add_to_cart'](101, 1)
    assert 'error' in result
    assert cart.items == {}

    tools['show_catalog']('violão', 'Giannini GNF-3', '', 0)
    tools['add_to_cart'](101, 1)
    assert cart.items == {101: 1}


def test_model_cant_show_and_confirm_in_the_same_message(temp_logs):
    # real attack: an injected message made the model call show_cart and confirm_order together
    cart = Cart()
    cart.add(TELECASTER, 15)
    cart.ask_confirmation()

    assert 'error' in cart.confirm()
    assert cart.order is None
    assert team_lines(temp_logs) == []

    cart.new_turn()
    assert cart.confirm()['status'] == 'sent'


def test_confirm_button_works_right_after_the_cart_shows_up():
    cart = Cart()
    cart.add(TELECASTER, 1)
    cart.ask_confirmation()
    assert cart.confirm(by_customer=True)['status'] == 'sent'


def test_order_cant_be_changed_forever(temp_logs):
    cart = confirmed_cart()
    for _ in range(MAX_VERSIONS - 1):
        cart.add(YAMAHA_C40, 1)
        assert show_and_confirm(cart)['status'] == 'updated'

    cart.add(YAMAHA_C40, 1)
    result = show_and_confirm(cart)
    assert 'error' in result
    assert cart.order['version'] == MAX_VERSIONS


def test_two_orders_get_different_numbers():
    numbers = set()
    for _ in range(20):
        numbers.add(confirmed_cart().order['number'])
    assert len(numbers) == 20
