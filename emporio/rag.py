import re

from emporio.settings import POLICIES_FILE
from emporio.text_utils import normalize

TOP_K = 3
STEM_SIZE = 4  # 'hora' matches 'horas' and 'horário', 'devo' matches 'devolver' and 'devolução'
TITLE_WEIGHT = 3
MAX_QUESTION = 300

# words that show up everywhere and don't help to pick a section
STOPWORDS = (
    'a o as os um uma de da do das dos e em no na nos nas para pra por com sem que qual quais '
    'como quando onde se ou voce voces eu meu minha meus minhas seu sua tem ter sao ser esta '
    'estao posso pode podem quero gostaria saber sobre mais muito loja emporio musica ola oi '
    'bom boa dia tarde noite obrigado'
).split()


def get_stems(text):
    stems = []
    for word in re.findall(r'[a-z0-9]+', normalize(text)):
        if len(word) > 1 and word not in STOPWORDS:
            stems.append(word[:STEM_SIZE])
    return stems


def count_words(words):
    counts = {}
    for word in words:
        counts[word] = counts.get(word, 0) + 1
    return counts


def new_section(title, lines):
    text = '\n'.join(lines).strip()
    return {
        'title': title,
        'text': text,
        'title_stems': get_stems(title),
        'body_stems': count_words(get_stems(text)),
    }


def load_sections():
    # splits the manual by its headings: every '##' or '###' starts a new section
    manual = POLICIES_FILE.read_text(encoding='utf-8')
    manual = re.sub(r'<!--.*?-->', '', manual, flags=re.DOTALL)

    sections = []
    parent = ''
    title = ''
    lines = []

    for line in manual.splitlines():
        if line.startswith('# '):
            continue

        if line.startswith('## ') or line.startswith('### '):
            if title and ''.join(lines).strip():
                sections.append(new_section(title, lines))
            lines = []

            heading = line.lstrip('#').strip()
            if line.startswith('## '):
                parent = heading
                title = heading
            else:
                # '4.1 Direito de Arrependimento' alone doesn't say it's about returns
                title = f'{parent} > {heading}'
        else:
            lines.append(line)

    if title and ''.join(lines).strip():
        sections.append(new_section(title, lines))
    return sections


SECTIONS = load_sections()


def get_score(item):
    return item[0]


def section_score(section, query):
    score = 0
    for stem in query:
        score += section['body_stems'].get(stem, 0)
        if stem in section['title_stems']:
            score += TITLE_WEIGHT
    return score


def search_policies(question: str) -> dict:
    """Searches the store policy manual and returns the most relevant sections. Use it for any
    question about the store itself: address, contact, opening hours, payment methods, installment
    rules, returns and exchanges, shipping, tracking, promotions rules, warranty and privacy (LGPD).

    Args:
        question: the customer's question or its main keywords, in Portuguese.
    """
    question = str(question or '')[:MAX_QUESTION]
    query = set(get_stems(question))

    scored = []
    for section in SECTIONS:
        score = section_score(section, query)
        if score > 0:
            scored.append((score, section['title'], section['text']))

    # sort by score only: on a tie the section that comes first in the manual stays first
    scored.sort(key=get_score, reverse=True)

    found = []
    for item in scored[:TOP_K]:
        found.append({'section': item[1], 'text': item[2]})

    if not found:
        return {'sections': [], 'note': 'Nada encontrado no manual para essa pergunta.'}
    return {'sections': found}
