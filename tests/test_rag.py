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


# natural questions, synonyms and close subjects. They are the evidence that keyword search is
# enough for this manual; if a question here needs meaning and not words, embeddings come next
@pytest.mark.parametrize('question, section', [
    ('Onde vocês ficam?', '1.2 Dados da Empresa'),
    ('onde fica a loja', '1.2 Dados da Empresa'),
    ('qual a localização?', '1.2 Dados da Empresa'),
    ('qual o endereço?', '1.2 Dados da Empresa'),
    ('vocês são confiáveis?', '1.2 Dados da Empresa'),
    ('qual o cnpj?', '1.2 Dados da Empresa'),
    ('abre domingo?', '2. Horário de Funcionamento'),
    ('aceita cartão?', '3. Formas de Pagamento'),
    ('quero devolver', '4.1 Direito de Arrependimento'),
    ('como faço pra trocar?', '4. Política de Trocas e Devoluções'),
    ('meu violão veio com defeito', '4.2 Trocas por Defeito'),
    ('tem garantia?', '8. Garantia'),
    ('vocês entregam em Dourados?', '5. Política de Frete e Entregas'),
])
def test_natural_questions_find_the_right_section(question, section):
    first = search_policies(question)['sections'][0]['section']
    assert section in first


def test_close_words_dont_pull_the_wrong_section():
    # "chegou" (arrived) is not "como chego" (how do I get there)
    first = search_policies('meu pedido chegou quebrado')['sections'][0]['section']
    assert 'Dados da Empresa' not in first


def test_subject_not_in_the_manual_finds_nothing():
    # the manual says nothing about picking up at the store: better nothing than a wrong section
    assert search_policies('posso retirar na loja?')['sections'] == []
