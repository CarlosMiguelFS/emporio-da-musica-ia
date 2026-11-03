import logging
import os
import time
import warnings

import main
from emporio import observability
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


def test_saved_conversation_is_masked_and_out_of_the_examples_folder(tmp_path, monkeypatch):
    # --save wrote the CPF and e-mail the customer typed into conversations/
    monkeypatch.setattr(observability, 'LOG_DIR', tmp_path)
    history = [('meu cpf é 123.456.789-00 e meu email ana@teste.com', [], 'Não precisa de CPF, tá?')]

    main.save(history)

    saved = list((tmp_path / 'conversations').glob('*.md'))
    assert len(saved) == 1
    text = saved[0].read_text(encoding='utf-8')
    assert '123.456.789-00' not in text
    assert 'ana@teste.com' not in text
    assert '[CPF removido]' in text


def test_old_saved_conversations_are_deleted(tmp_path, monkeypatch):
    monkeypatch.setattr(observability, 'LOG_DIR', tmp_path)
    old = tmp_path / 'conversations' / 'conversation_old.md'
    old.parent.mkdir()
    old.write_text('old', encoding='utf-8')
    ages_ago = time.time() - 40 * 24 * 3600
    os.utime(old, (ages_ago, ages_ago))

    observability.purge_old_logs()
    assert not old.exists()
