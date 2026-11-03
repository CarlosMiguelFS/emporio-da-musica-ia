from datetime import date, timedelta

import pytest

from emporio import tools
from emporio.tools import (
    ORDER_LOCKED,
    ORDER_NOT_FOUND,
    active_promotions,
    days_since_received,
    get_order,
    installment_options,
    search_products,
)


@pytest.fixture(autouse=True)
def reset_attempts():
    tools.shared_attempts['failed'] = 0
    tools.order_fails.clear()
    tools.server_fails.clear()


def test_search_by_model():
    result = search_products('takamine gd20', '', 0, False)
    product = result['products'][0]
    assert product['name'] == 'Takamine GD20 Dreadnought Natural'
    assert product['price'] == 2199
    assert product['pix_price'] == pytest.approx(2089.05)


def test_search_ignores_accents():
    assert search_products('violao', 'violoes', 0, False)['total_found'] > 0


def test_available_guitars_under_1000():
    result = search_products('', 'Violões', 1000, True)
    assert result['total_found'] > 0
    for p in result['products']:
        assert p.get('promo_price', p['price']) <= 1000
        assert p['stock'] > 0
        assert p['status'] == 'ativo'


def test_discontinued_shows_when_asked_by_name():
    product = search_products('shelby sn-7c', '', 0, False)['products'][0]
    assert product['status'] == 'descontinuado'


def test_promotion_has_no_pix_discount():
    # product 90 has an active 18% promotion
    product = search_products('crafter ht-100', '', 0, False)['products'][0]
    assert product['discount_percent'] == 18
    assert product['promo_price'] == pytest.approx(1967.18)
    assert 'pix_price' not in product


def test_only_active_promotions():
    ids = {p['id'] for p in active_promotions()['products']}
    assert ids == {90, 94, 121, 127}


def test_search_handles_bad_arguments():
    assert search_products('.*' * 500, None, 'abc', False)['total_found'] == 0
    assert search_products('', '', -50, True)['total_found'] > 0


def test_order_with_right_name():
    order = get_order(1, 'Pedro')
    assert order['status'] == 'entregue'
    assert order['tracking_code'] == 'BRAB1234567BR'
    # delivered: the forecast is left out, the real day is asked to the customer
    assert 'estimated_delivery' not in order
    assert 'days_since_delivery' not in order


def test_order_never_shows_contact_data():
    order = str(get_order(1, 'Pedro'))
    assert '@' not in order
    assert '98432' not in order


def test_wrong_name_and_missing_order_look_the_same():
    assert get_order(1, 'Ana') == {'error': ORDER_NOT_FOUND}
    assert get_order(999, 'Pedro') == {'error': ORDER_NOT_FOUND}
    assert get_order('abc', 'Pedro') == {'error': ORDER_NOT_FOUND}


def test_order_locks_after_failed_attempts():
    for _ in range(3):
        get_order(1, 'Ana')
    assert get_order(1, 'Pedro') == {'error': ORDER_LOCKED}


def test_right_answer_does_not_reset_the_count():
    # real attack: fail twice, look up your own order to reset, try again, forever
    lookup = tools.order_tool(tools.new_attempts())
    lookup(4, 'Ana')
    lookup(8, 'Lucas')
    assert 'status' in lookup(1, 'Pedro')
    lookup(5, 'Maria')
    assert lookup(1, 'Pedro') == {'error': ORDER_LOCKED}


def test_one_customer_failing_does_not_lock_the_others():
    attacker = tools.order_tool(tools.new_attempts())
    customer = tools.order_tool(tools.new_attempts())
    for order_id in [2, 3, 4]:
        attacker(order_id, 'Ana')

    assert attacker(1, 'Pedro') == {'error': ORDER_LOCKED}
    assert customer(1, 'Pedro')['status'] == 'entregue'


def test_same_order_locks_even_from_new_conversations():
    # opening a new tab for each try doesn't help
    for _ in range(3):
        tools.order_tool(tools.new_attempts())(1, 'Ana')
    assert tools.order_tool(tools.new_attempts())(1, 'Pedro') == {'error': ORDER_LOCKED}


def test_too_many_fails_on_the_server_stops_lookups():
    for order_id in range(100, 100 + tools.MAX_FAILS_ON_SERVER):
        tools.order_tool(tools.new_attempts())(order_id, 'Ana')
    assert tools.order_tool(tools.new_attempts())(1, 'Pedro') == {'error': ORDER_LOCKED}


def test_lock_ends_after_some_time(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(tools, 'now', lambda: clock[0])
    for _ in range(3):
        tools.order_tool(tools.new_attempts())(1, 'Ana')
    assert tools.order_tool(tools.new_attempts())(1, 'Pedro') == {'error': ORDER_LOCKED}

    clock[0] += tools.LOCK_SECONDS + 1
    assert tools.order_tool(tools.new_attempts())(1, 'Pedro')['status'] == 'entregue'


def test_random_order_numbers_are_not_kept_in_memory():
    for order_id in range(5000, 5010):
        get_order(order_id, 'Ana')
    assert tools.order_fails == {}


def test_conversation_tools_have_their_own_get_order():
    names = []
    for tool in tools.conversation_tools():
        names.append(tool.__name__)
        if tool.__name__ == 'get_order':
            assert tool is not get_order
            assert tool.__doc__ == get_order.__doc__
    assert names == ['search_products', 'active_promotions', 'get_order', 'installment_options', 'search_policies',
                     'days_since_received']


def test_cancelled_order_has_reason():
    order = get_order(18, 'gustavo nunes')
    assert order['status'] == 'cancelado'
    assert order['notes'] == 'Item fora de estoque no momento do pedido'
    assert 'tracking_code' not in order


def test_installments():
    assert max(o['installments'] for o in installment_options(100)['interest_free_options']) == 2
    assert max(o['installments'] for o in installment_options(1000)['interest_free_options']) == 10
    assert installment_options(2500)['can_combine_payment_methods']
    assert 'error' in installment_options(-10)


def test_customer_contact_data_is_never_loaded():
    from emporio.data import CUSTOMERS

    assert list(CUSTOMERS.columns) == ['customer_id', 'name']


def test_days_since_received_counts_from_the_date_the_customer_gives():
    today = date.today()
    result = days_since_received((today - timedelta(days=3)).isoformat())
    assert result['days_since_received'] == 3
    assert result['within_7_days'] is True

    result = days_since_received((today - timedelta(days=8)).isoformat())
    assert result['within_7_days'] is False


@pytest.mark.parametrize('value', ['ontem', '18/02/2026', '', None])
def test_days_since_received_refuses_a_date_it_cant_read(value):
    assert 'error' in days_since_received(value)


def test_days_since_received_refuses_a_future_date():
    assert 'error' in days_since_received((date.today() + timedelta(days=2)).isoformat())


def test_order_on_the_way_shows_the_forecast_as_a_forecast():
    order = get_order(8, 'Ana')
    assert order['status'] == 'enviado'
    assert order['estimated_delivery'] == '2026-02-28'
    assert 'previsão' in order['estimated_delivery_note']
