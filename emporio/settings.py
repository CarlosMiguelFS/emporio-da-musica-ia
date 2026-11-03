from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
RAW_DATA_DIR = ROOT_DIR / 'data' / 'raw'
POLICIES_FILE = ROOT_DIR / 'data' / 'politicas.md'
LOG_DIR = ROOT_DIR / 'logs'
CONVERSATIONS_DIR = ROOT_DIR / 'conversations'

STORE_PHONE = '(67) 3341-4444'
STORE_WHATSAPP = '(67) 3321-4500'
STORE_EMAIL = 'contato@emporiodamusica.com.br'

MAX_TURNS = 30  # per session, so a bug or an abuse doesn't turn into a big bill
# names typed in the chat stay in the log, so no "no personal data" promise
PRIVACY_NOTICE = (
    'Esta conversa é registrada para melhorar o atendimento. Não guardamos CPF, cartão, e-mail '
    'nem telefone, e os registros são apagados em 30 dias.'
)
WELCOME = 'Oi! Sou o assistente virtual da Empório da Música. Como posso te ajudar hoje?'
