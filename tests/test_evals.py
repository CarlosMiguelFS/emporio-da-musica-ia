import pytest

from emporio import agent, guardrails, observability
from emporio.cart import Cart
from evals import run_evals


class FakeChat:
    model = 'fake-model'

    def __init__(self):
        self.cart = Cart()
        self.fallbacks = []


@pytest.fixture(autouse=True)
def temp_logs(tmp_path, monkeypatch):
    monkeypatch.setattr(observability, 'LOG_DIR', tmp_path)


def _fake_agent(monkeypatch, answers):
    answers = list(answers)
    monkeypatch.setattr(agent, 'new_chat', FakeChat)
    monkeypatch.setattr(agent, 'reply', lambda chat, session, message: (answers.pop(0), []))


def test_error_in_the_first_answer_fails_even_if_the_last_one_is_fine(monkeypatch):
    # audit finding: only the last answer was checked, a broken first answer went unnoticed
    _fake_agent(monkeypatch, [agent.ERROR_REPLY, 'Que bom que deu certo!'])
    case = {'id': 'two_messages', 'messages': ['oi', 'valeu']}

    result = run_evals.run_case(case, 1, 'test')

    assert 'message 1: technical error (see logs/evals)' in result['problems']


def test_blocked_price_in_the_middle_of_the_chat_is_reported(monkeypatch):
    _fake_agent(monkeypatch, ['Oi!', guardrails.UNCONFIRMED_PRICE_REPLY, 'Beleza!'])
    case = {'id': 'three_messages', 'messages': ['oi', 'quanto custa?', 'ok']}

    result = run_evals.run_case(case, 1, 'test')

    assert 'message 2: price blocked twice: the customer got no price' in result['problems']


def test_last_answer_is_still_checked_against_the_case(monkeypatch):
    _fake_agent(monkeypatch, ['Oi!', 'Abrimos às 9h.'])
    case = {'id': 'hours', 'messages': ['oi', 'que horas abre?'], 'must_contain': ['13']}

    result = run_evals.run_case(case, 1, 'test')

    assert "answer is missing '13'" in result['problems']


def test_critical_cases_run_more_than_once(monkeypatch):
    _fake_agent(monkeypatch, ['Oi!', 'Oi!', 'Oi!'])
    case = {'id': 'critical', 'messages': ['oi'], 'runs': 3}

    results = run_evals.run([case], runs=1)

    assert len(results) == 3
