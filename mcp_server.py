import re

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from emporio.cart import Cart, cart_text, catalog_text
from emporio.tools import TOOLS

server = MCPServer(
    'emporio-da-musica',
    instructions='Tools for the Empório da Música store: data, policies, catalog and cart. '
                 'Use the same session_id for the whole conversation with one customer. '
                 'Only call confirm_order after the customer said the cart is right.',
)

read_only = ToolAnnotations(read_only_hint=True, open_world_hint=False)
changes_cart = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)
# sending the order is marked as destructive so MCP clients ask the person before running it
sends_order = ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=True)

for tool in TOOLS:
    register = server.tool(annotations=read_only)
    register(tool)

# one cart per session, in memory
carts = {}
MAX_CARTS = 500
SESSION_PATTERN = re.compile(r'[A-Za-z0-9_-]{8,64}')
BAD_SESSION = {'error': 'session_id inválido: use de 8 a 64 letras, números, - ou _.'}


def get_cart(session_id):
    session_id = str(session_id)
    if not SESSION_PATTERN.fullmatch(session_id):
        return None

    if session_id not in carts:
        # the oldest cart goes away, so a lot of fake sessions can't fill the memory
        if len(carts) >= MAX_CARTS:
            oldest = next(iter(carts))
            del carts[oldest]
        carts[session_id] = Cart(session_id)

    cart = carts[session_id]
    # each call counts as a new message, so show_cart and confirm_order can't be the same step
    cart.new_turn()
    return cart


def with_text(cart, result):
    # ready text for clients without a screen
    lines = []
    for event in cart.take_events():
        if event['kind'] == 'catalog':
            lines.append(catalog_text(event['catalog']))
        if event['kind'] == 'cart' and cart.items:
            lines.append(cart_text(cart))
    if lines:
        result['text'] = '\n\n'.join(lines)
    return result


@server.tool(annotations=changes_cart)
def show_catalog(session_id: str, categories: str, term: str = '', brand: str = '', max_price: float = 0) -> dict:
    """Builds a catalog of available products, grouped by brand and numbered, for the customer
    to choose from. With many products it returns a summary by brand: ask brand or budget.

    Args:
        session_id: id of the conversation, the same in every call.
        categories: one or more categories separated by commas, e.g. "Guitarras, Violões".
        term: model the customer named, shown first. "" for none.
        brand: only this brand. "" for all.
        max_price: maximum price in BRL. 0 for no limit.
    """
    cart = get_cart(session_id)
    if cart is None:
        return BAD_SESSION
    return with_text(cart, cart.catalog(categories, term, brand, max_price))


@server.tool(annotations=changes_cart)
def add_to_cart(session_id: str, product_id: int, quantity: int) -> dict:
    """Adds a product to the cart; adding it again adds up the quantity.

    Args:
        session_id: id of the conversation.
        product_id: product_id from show_catalog or search_products.
        quantity: units to add.
    """
    cart = get_cart(session_id)
    if cart is None:
        return BAD_SESSION
    return with_text(cart, cart.add_from_model(product_id, quantity))


@server.tool(annotations=changes_cart)
def pick_from_catalog(session_id: str, number: int, quantity: int) -> dict:
    """Adds the product with this number in the last catalog ("quero o 2").

    Args:
        session_id: id of the conversation.
        number: the catalog number the customer said.
        quantity: units to add.
    """
    cart = get_cart(session_id)
    if cart is None:
        return BAD_SESSION
    return with_text(cart, cart.pick(number, quantity))


@server.tool(annotations=changes_cart)
def change_quantity(session_id: str, product_id: int, quantity: int) -> dict:
    """Sets the quantity of a product in the cart. 0 removes it.

    Args:
        session_id: id of the conversation.
        product_id: product_id of the item.
        quantity: new quantity.
    """
    cart = get_cart(session_id)
    if cart is None:
        return BAD_SESSION
    return with_text(cart, cart.set_quantity(product_id, quantity))


@server.tool(annotations=changes_cart)
def show_cart(session_id: str) -> dict:
    """Shows the cart with the totals so the customer can check it before confirming.

    Args:
        session_id: id of the conversation.
    """
    cart = get_cart(session_id)
    if cart is None:
        return BAD_SESSION
    return with_text(cart, cart.ask_confirmation())


@server.tool(annotations=sends_order)
def confirm_order(session_id: str) -> dict:
    """Sends the order to a store attendant, who creates the payment link. Only after show_cart
    and after the customer said the cart is right.

    Args:
        session_id: id of the conversation.
    """
    cart = get_cart(session_id)
    if cart is None:
        return BAD_SESSION
    return with_text(cart, cart.confirm())


if __name__ == '__main__':
    # stdio only: the server is started by the MCP client on the same machine, no open port
    server.run(transport='stdio')
