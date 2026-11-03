import pytest

from emporio import tools
from emporio.tools import (
    ORDER_LOCKED,
    ORDER_NOT_FOUND,
    active_promotions,
    get_order,
    installment_options,
    search_products,
)


@pytest.fixture(autouse=True)
def reset_attempts():
    tools.failed_attempts = 0


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
    assert 'days_since_delivery' in order


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
