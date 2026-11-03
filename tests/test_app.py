import pytest
from streamlit.testing.v1 import AppTest

from emporio import agent, chat_ui, observability
from emporio.cart import Cart
from emporio.settings import WELCOME


class FakeResponse:
    usage_metadata = None

    def __init__(self, text):
        self.text = text


class FakeChat:
    model = 'fake-model'

    def __init__(self, answer=None, error=None, on_message=None):
        self.answer = answer
        self.error = error
        self.fallbacks = []
        self.cart = Cart()
        # what the fake "model" does with the cart, like calling show_catalog
        self.on_message = on_message

    def send_message(self, message):
        if self.error:
            raise self.error
        if self.on_message:
            self.on_message(self.cart, message)
        return FakeResponse(self.answer)

    def get_history(self):
        return []


@pytest.fixture(autouse=True)
def temp_logs(tmp_path, monkeypatch):
    monkeypatch.setattr(observability, 'LOG_DIR', tmp_path)


@pytest.fixture(autouse=True)
def no_typing_wait(monkeypatch):
    # no typing wait in tests (Streamlit's test runner needs the real sleep)
    monkeypatch.setattr(chat_ui, 'wait', lambda seconds: None)


def _open_app(monkeypatch, new_chat):
    monkeypatch.setattr(agent, 'new_chat', new_chat)
    app = AppTest.from_file('../app.py', default_timeout=20)
    app.run()
    return app


def _page_text(app):
    return ' '.join(str(m.value) for m in app.markdown) + ' '.join(str(e.value) for e in app.error)


def test_customer_message_gets_the_agent_answer(monkeypatch):
    app = _open_app(monkeypatch, lambda: FakeChat(answer='Oi! Como posso ajudar?'))
    app.chat_input[0].set_value('boa tarde').run()

    assert not app.exception
    assert app.chat_message[-1].markdown[0].value == 'Oi! Como posso ajudar?'


def test_api_error_shows_only_the_friendly_message(monkeypatch):
    error = RuntimeError('402 RESOURCE_EXHAUSTED https://generativelanguage.googleapis.com')
    app = _open_app(monkeypatch, lambda: FakeChat(error=error))
    app.chat_input[0].set_value('oi').run()

    assert not app.exception
    assert app.chat_message[-1].markdown[0].value == agent.ERROR_REPLY
    assert 'googleapis' not in _page_text(app)


def test_missing_key_shows_only_the_friendly_message(monkeypatch):
    def no_key():
        raise SystemExit('GEMINI_API_KEY is empty. Put your key in the .env file.')

    app = _open_app(monkeypatch, no_key)

    assert not app.exception
    assert app.error[0].value == agent.ERROR_REPLY
    assert 'GEMINI' not in _page_text(app)


def test_first_screen_has_privacy_notice_and_welcome(monkeypatch):
    app = _open_app(monkeypatch, lambda: FakeChat(answer='ok'))

    assert any('apagados em 30 dias' in c.value for c in app.caption)
    assert app.chat_message[0].markdown[0].value == WELCOME


def test_real_sign_is_not_eaten_by_markdown(monkeypatch):
    # no numbers, so the price guardrail has nothing to check and the text goes through as is
    app = _open_app(monkeypatch, lambda: FakeChat(answer='Os preços são em R$ e no PIX também em R$.'))
    app.chat_input[0].set_value('quanto custa?').run()

    shown = app.chat_message[-1].markdown[0].value
    assert shown.count(r'\$') == 2  # escaped, so Streamlit shows "R$" instead of a math formula


def test_model_text_is_never_rendered_as_html(monkeypatch):
    app = _open_app(monkeypatch, lambda: FakeChat(answer='<img src=x onerror=alert(1)> oi'))
    app.chat_input[0].set_value('oi').run()

    answer = app.chat_message[-1].markdown[0]
    assert answer.proto.allow_html is False


def test_typing_time_grows_with_the_answer_but_has_a_cap():
    short = chat_ui.typing_time('Oi!')
    medium = chat_ui.typing_time('x' * 300)
    huge = chat_ui.typing_time('x' * 5000)

    assert short < medium
    assert huge == chat_ui.MAX_TYPING_SECONDS


def _model_shows_catalog(cart, message):
    if 'violão' in message:
        cart.catalog('violão', '', 'Yamaha', 0)
    if 'guitarra' in message:
        cart.catalog('guitarra', '', '', 0)
    if 'só isso' in message:
        cart.ask_confirmation()


def test_catalog_button_shows_up_and_opens_the_products(monkeypatch):
    app = _open_app(monkeypatch, lambda: FakeChat(answer='Separei os Yamaha.', on_message=_model_shows_catalog))
    app.chat_input[0].set_value('quero um violão').run()

    assert not app.exception
    button = app.button[-1]
    assert button.label == 'Ver catálogo (7 modelos)'

    button.click().run()
    assert not app.exception
    assert len(app.checkbox) == 7


def test_new_catalog_closes_the_old_one(monkeypatch):
    app = _open_app(monkeypatch, lambda: FakeChat(answer='ok', on_message=_model_shows_catalog))
    app.chat_input[0].set_value('quero um violão').run()
    app.chat_input[0].set_value('quero uma guitarra').run()

    labels = []
    for button in app.button:
        labels.append(button.label)
    assert labels == ['Catálogo encerrado', 'Ver catálogo (5 modelos)']


def test_cart_buttons_change_quantity_and_confirm(monkeypatch, temp_logs):
    app = _open_app(monkeypatch, lambda: FakeChat(answer='ok', on_message=_model_shows_catalog))
    cart = app.session_state.chat.cart
    cart.add(81, 1)
    app.chat_input[0].set_value('só isso').run()

    plus = None
    for button in app.button:
        if button.label == '+':
            plus = button
    plus.click().run()
    assert cart.items == {81: 2}

    for button in app.button:
        if button.label == 'Confirmar pedido':
            button.click().run()
    assert not app.exception
    assert cart.order['status'] == 'enviado para a equipe'
    # the model gets told, so it can go on with the conversation
    assert app.chat_message[-2].markdown[0].value == 'Confirmo o pedido.'
