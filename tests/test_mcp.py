import asyncio

import pytest

import mcp_server
from emporio import observability


@pytest.fixture(autouse=True)
def temp_logs(tmp_path, monkeypatch):
    monkeypatch.setattr(observability, 'LOG_DIR', tmp_path)
    mcp_server.carts.clear()


def test_every_tool_is_registered():
    tools = asyncio.run(mcp_server.server.list_tools())
    names = []
    for tool in tools:
        names.append(tool.name)
    assert names == ['search_products', 'active_promotions', 'get_order', 'installment_options', 'search_policies',
                     'days_since_received', 'show_catalog', 'add_to_cart', 'change_quantity', 'show_cart',
                     'confirm_order']


def test_mcp_keeps_one_cart_per_session():
    async def call(name, args):
        return await mcp_server.server.call_tool(name, args)

    asyncio.run(call('show_catalog', {'session_id': 'sessao-aaaa', 'categories': 'ukulele', 'brand': 'Kalani'}))
    asyncio.run(call('add_to_cart', {'session_id': 'sessao-aaaa', 'product_id': 127, 'quantity': 2}))
    result = asyncio.run(call('show_cart', {'session_id': 'sessao-bbbb'}))

    assert mcp_server.carts['sessao-aaaa'].items == {127: 2}
    assert mcp_server.carts['sessao-bbbb'].items == {}
    assert 'vazio' in str(result)


def test_mcp_answers_have_text_for_clients_without_screen():
    result = asyncio.run(mcp_server.server.call_tool(
        'show_catalog', {'session_id': 'sessao-cccc', 'categories': 'guitarra'}))
    assert '1. Fender Telecaster Player Series' in str(result)


def call(name, args):
    return asyncio.run(mcp_server.server.call_tool(name, args))


@pytest.mark.parametrize('session_id', ['a', '../../etc', 'x' * 100, 'sessão com espaço'])
def test_bad_session_id_is_refused(session_id):
    result = call('show_cart', {'session_id': session_id})
    assert 'inválido' in str(result)
    assert mcp_server.carts == {}


def test_show_and_confirm_need_two_calls(monkeypatch):
    call('show_catalog', {'session_id': 'sessao-dddd', 'categories': 'violão', 'brand': 'Yamaha'})
    call('add_to_cart', {'session_id': 'sessao-dddd', 'product_id': 81, 'quantity': 1})
    call('show_cart', {'session_id': 'sessao-dddd'})
    call('confirm_order', {'session_id': 'sessao-dddd'})
    assert mcp_server.carts['sessao-dddd'].order['status'] == 'enviado para a equipe'


def test_fake_sessions_cant_fill_the_memory(monkeypatch):
    monkeypatch.setattr(mcp_server, 'MAX_CARTS', 5)
    for number in range(20):
        call('show_cart', {'session_id': f'sessao-{number:04d}'})
    assert len(mcp_server.carts) == 5
    assert 'sessao-0019' in mcp_server.carts


def test_confirm_order_asks_the_person_in_mcp_clients():
    tools = asyncio.run(mcp_server.server.list_tools())
    for tool in tools:
        if tool.name == 'confirm_order':
            assert tool.annotations.destructive_hint is True
        elif tool.name in ['search_products', 'get_order', 'search_policies']:
            assert tool.annotations.read_only_hint is True


def test_mcp_cant_add_a_product_that_was_never_shown():
    result = call('add_to_cart', {'session_id': 'sessao-eeee', 'product_id': 101, 'quantity': 1})
    assert 'catálogo' in str(result)
    assert mcp_server.carts['sessao-eeee'].items == {}
