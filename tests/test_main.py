import logging
import warnings

import main
from emporio.cart import Cart


def test_library_logs_and_warnings_never_reach_the_screen(capfd):
    main.silence_internal_output()
    logging.getLogger('google_genai.models').warning('POST https://generativelanguage.googleapis.com 402')
    logging.getLogger('httpx').error('HTTP Request failed')
    warnings.warn('use_json_schema is not True', UserWarning, stacklevel=1)

    out, err = capfd.readouterr()
    assert out == '' and err == ''


def test_terminal_shows_catalog_and_cart_after_the_answer():
    cart = Cart()
    cart.catalog('violão', '', 'Yamaha', 0)
    cart.add(81, 1)
    cart.ask_confirmation()

    text = main.show_events(cart)
    assert '1. Yamaha C40' in text
    assert 'quero o 2' in text
    assert 'confirmo' in text
    assert main.show_events(cart) == ''
