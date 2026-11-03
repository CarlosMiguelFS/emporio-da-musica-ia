import time

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

from emporio import chat_ui  # noqa: E402
from emporio.agent import ERROR_REPLY, new_chat, reply  # noqa: E402
from emporio.cart import brl, price_text  # noqa: E402
from emporio.observability import describe_error, log_turn, new_session_id, purge_old_logs  # noqa: E402
from emporio.settings import MAX_TURNS, PRIVACY_NOTICE, STORE_PHONE, WELCOME  # noqa: E402
from emporio.text_utils import escape_markdown_dollars  # noqa: E402

AVATARS = {'user': '👤', 'assistant': '🎼'}
BRAND_FILTER_FROM = 6


def money(text):
    return escape_markdown_dollars(text)


@st.dialog('Catálogo', width='large')
def show_catalog(catalog):
    cart = st.session_state.chat.cart
    selected = []

    for index, section in enumerate(catalog['sections']):
        if not section['brands']:
            continue
        key = f"{catalog['id']}_{index}"
        st.subheader(f"{section['category']} ({section['total']})", anchor=False)

        groups = section['brands']
        if section['total'] >= BRAND_FILTER_FROM:
            names = []
            for group in groups:
                names.append(group['brand'])
            picked = st.pills('Marca', names, selection_mode='multi', key=f'brand_{key}')
            if picked:
                filtered = []
                for group in groups:
                    if group['brand'] in picked:
                        filtered.append(group)
                groups = filtered

        for group in groups:
            if not group['items']:
                continue
            st.markdown(f"**{group['brand']}**")
            for item in group['items']:
                product = item['product']
                with st.container(border=True):
                    left, right = st.columns([4, 1])
                    item_key = f"{key}_{product['id']}"
                    with left:
                        st.markdown(product['name'])
                        st.caption(money(f"{price_text(product)} · {product['stock']} em estoque"))
                    with right:
                        chosen = st.checkbox('Quero', key=f'check_{item_key}')
                        quantity = st.number_input('Qtd', 1, product['stock'], 1, key=f'qty_{item_key}',
                                                   disabled=not chosen)
                    if chosen:
                        selected.append((product, quantity))

    if catalog['total'] < catalog['all_products']:
        shown = f"Mostrando {catalog['total']} de {catalog['all_products']}."
        st.caption(f'{shown} Me fala a marca ou o valor no chat que eu filtro.')

    st.divider()
    if cart.items:
        st.caption(money(f'Já no carrinho: {brl(cart.total())}'))

    if st.button('Adicionar ao carrinho', type='primary', disabled=not selected, use_container_width=True):
        added = []
        for product, quantity in selected:
            cart.add_from_screen(product['id'], quantity)
            added.append(f"{product['name']} x{quantity}")
        # tell the model what was picked
        st.session_state.pending = 'Adicionei ao carrinho: ' + ', '.join(added)
        st.session_state.active_catalog = None
        st.rerun()


def show_cart():
    cart = st.session_state.chat.cart
    with st.container(border=True):
        for line in cart.lines():
            product = line['product']
            name, minus, quantity, plus = st.columns([5, 1, 1, 1], vertical_alignment='center')
            name.markdown(money(f"{product['name']}  \n{brl(line['value'])}"))
            if minus.button('-', key=f"minus_{product['id']}"):
                cart.change_by(product['id'], -1)
                st.rerun()
            quantity.markdown(f"**{line['quantity']}**")
            if plus.button('+', key=f"plus_{product['id']}", disabled=line['quantity'] >= product['stock']):
                cart.change_by(product['id'], 1)
                st.rerun()

        if not cart.items:
            st.markdown('Carrinho vazio.')
            return

        st.markdown(money(f'**Total: {brl(cart.total())}** (no PIX: {brl(cart.pix_total())})'))
        if st.button('Confirmar pedido', type='primary'):
            result = cart.confirm(by_customer=True)
            if 'error' not in result:
                st.session_state.pending = 'Confirmo o pedido.'
            st.rerun()


def show_message(message):
    with st.chat_message(message['role'], avatar=AVATARS[message['role']]):
        st.markdown(escape_markdown_dollars(message['text']))

        catalog = message.get('catalog')
        if catalog and catalog['total'] > 0:
            if catalog['id'] == st.session_state.active_catalog:
                if st.button(f"Ver catálogo ({catalog['total']} modelos)", key=f"open_{catalog['id']}"):
                    show_catalog(catalog)
            else:
                st.button('Catálogo encerrado', key=f"open_{catalog['id']}", disabled=True)

        if message.get('cart') == st.session_state.cart_count and st.session_state.chat.cart.waiting_confirmation:
            show_cart()


def answer(text):
    st.session_state.messages.append({'role': 'user', 'text': text})
    show_message(st.session_state.messages[-1])

    with st.chat_message('assistant', avatar=AVATARS['assistant']):
        bubble = st.empty()
        bubble.markdown(chat_ui.TYPING_DOTS, unsafe_allow_html=True)

        started = time.time()
        text, _ = reply(st.session_state.chat, st.session_state.session, text)

        # the model time already counts as typing, only wait for what is missing
        waited = time.time() - started
        chat_ui.wait(chat_ui.typing_time(text) - waited)

        bubble.markdown(escape_markdown_dollars(text))

    st.session_state.chat.cart.picked_on_screen = {}

    message = {'role': 'assistant', 'text': text}
    for event in st.session_state.chat.cart.take_events():
        if event['kind'] == 'catalog':
            # a new catalog closes the old one, the cart stays
            st.session_state.catalog_count += 1
            event['catalog']['id'] = st.session_state.catalog_count
            st.session_state.active_catalog = st.session_state.catalog_count
            message['catalog'] = event['catalog']
        if event['kind'] == 'cart':
            st.session_state.cart_count += 1
            message['cart'] = st.session_state.cart_count

    st.session_state.messages.append(message)
    if 'catalog' in message or 'cart' in message:
        st.rerun()


st.set_page_config(page_title='Empório da Música', page_icon='🎸')
st.title('Empório da Música', anchor=False)
st.caption('Atendimento virtual · Campo Grande/MS')
st.caption(PRIVACY_NOTICE)

# Streamlit runs this whole file again on every message. session_state is what survives
# between runs, and it is separate for each browser tab, so each customer has their own chat.
if 'chat' not in st.session_state:
    try:
        purge_old_logs()
        st.session_state.chat = new_chat()
    except BaseException as error:
        # new_chat exits when the key is missing; the customer sees no details, the team sees the log
        log_turn({'session': 'ui-startup', 'error': describe_error(error)})
        st.error(ERROR_REPLY)
        st.stop()

    st.session_state.session = new_session_id()
    st.session_state.chat.cart.session_id = st.session_state.session
    st.session_state.messages = [{'role': 'assistant', 'text': WELCOME}]
    st.session_state.pending = None
    st.session_state.active_catalog = None
    st.session_state.catalog_count = 0
    st.session_state.cart_count = 0

for message in st.session_state.messages:
    show_message(message)

customer_turns = 0
for message in st.session_state.messages:
    if message['role'] == 'user':
        customer_turns += 1

if customer_turns >= MAX_TURNS:
    st.warning(f'Limite de mensagens desta sessão atingido. Para continuar, fale com a equipe no {STORE_PHONE}.')
    st.stop()

text = st.chat_input('Digite sua mensagem...')
if st.session_state.pending:
    text = st.session_state.pending
    st.session_state.pending = None
if text:
    answer(text)
