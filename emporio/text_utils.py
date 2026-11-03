import unicodedata


def normalize(text):
    # "Violão" and "violao" need to match
    text = unicodedata.normalize('NFKD', str(text))
    return text.encode('ascii', 'ignore').decode().lower()


def escape_markdown_dollars(text):
    # Streamlit and GitHub read "$...$" as a math formula
    return str(text).replace('$', r'\$')
