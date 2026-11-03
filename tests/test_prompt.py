from datetime import datetime

import pytest

from emporio.prompt import STORE_TIME, build_prompt, store_status


def at(day, hour, minute=0):
    # November 2026: the 2nd is a Monday
    return datetime(2026, 11, day, hour, minute, tzinfo=STORE_TIME)


@pytest.mark.parametrize('now, expected', [
    (at(3, 7, 31), 'a loja está fechada agora e abre hoje às 09:00'),
    (at(3, 10), 'a loja está aberta agora, até as 18:00'),
    (at(3, 18, 30), 'a loja está fechada agora e abre amanhã (quarta-feira) às 09:00'),
    (at(6, 19), 'a loja está fechada agora e abre amanhã (sábado) às 09:00'),
    (at(7, 12), 'a loja está aberta agora, até as 13:00'),
    (at(7, 14), 'a loja está fechada agora e abre segunda-feira às 09:00'),
    (at(8, 10), 'a loja está fechada agora e abre amanhã (segunda-feira) às 09:00'),
])
def test_store_status_is_computed_in_python(now, expected):
    # audit finding: on a Tuesday before 9h the model said the team would be back on Wednesday
    assert store_status(now) == expected


def test_prompt_has_the_store_status_and_time_in_campo_grande():
    prompt = build_prompt(at(3, 7, 31))
    assert 'Store status now: a loja está fechada agora e abre hoje às 09:00' in prompt
    assert 'terça-feira, 03/11/2026 07:31 (horário de Campo Grande)' in prompt


def test_prompt_has_the_policy_conditions():
    prompt = build_prompt(at(3, 10))
    assert 'online purchases' in prompt
    assert '90 days' in prompt
    assert 'last_value' in prompt
