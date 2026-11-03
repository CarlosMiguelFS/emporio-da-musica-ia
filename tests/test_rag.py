import pytest

from emporio.rag import search_policies


@pytest.mark.parametrize(
    'question, expected',
    [
        ('qual o endereço da loja?', '1.2 Dados da Empresa'),
        ('que horas vocês abrem no sábado?', '2. Horário de Funcionamento'),
        ('me arrependi da compra, posso devolver?', '4.1 Direito de Arrependimento'),
        ('dá pra parcelar em 12x?', '3.1 Regras de Parcelamento'),
        ('quanto é o frete pra Dourados?', '5.2 Entregas para Outras Cidades'),
        ('quero apagar meus dados', '9. Privacidade'),
    ],
)
def test_finds_the_right_section(question, expected):
    first = search_policies(question)['sections'][0]['section']
    assert expected in first


def test_returns_at_most_three_sections():
    assert len(search_policies('troca devolução garantia frete pagamento')['sections']) == 3


def test_nothing_found():
    result = search_policies('xyzw')
    assert result['sections'] == []
