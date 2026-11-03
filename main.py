import argparse
import logging
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

from emporio.agent import ERROR_REPLY, new_chat, reply  # noqa: E402
from emporio.cart import cart_text, catalog_text  # noqa: E402
from emporio.observability import describe_error, log_turn, new_session_id, purge_old_logs  # noqa: E402
from emporio.settings import CONVERSATIONS_DIR, MAX_TURNS, PRIVACY_NOTICE, STORE_PHONE  # noqa: E402
from emporio.text_utils import escape_markdown_dollars  # noqa: E402


def silence_internal_output():
    # nothing technical on the customer's screen: library logs and warnings (URLs, model names,
    # error messages) would show what is behind the agent. Errors are in logs/ for the team.
    logging.getLogger().addHandler(logging.NullHandler())
    logging.captureWarnings(True)


def save(history):
    CONVERSATIONS_DIR.mkdir(exist_ok=True)
    now = datetime.now()
    path = CONVERSATIONS_DIR / f'conversation_{now:%Y%m%d_%H%M%S}.md'

    lines = [f'# Conversation — {now:%d/%m/%Y %H:%M}', '']
    for message, calls, answer in history:
        lines.append(f'**Customer:** {escape_markdown_dollars(message)}')
        lines.append('')
        for call in calls:
            lines.append(f'> tool: `{call}`')
        if calls:
            lines.append('')
        lines.append(f'**Agent:** {escape_markdown_dollars(answer)}')
        lines.append('')

    path.write_text('\n'.join(lines), encoding='utf-8')
    print(f'Conversation saved to {path}')


def show_events(cart):
    # no buttons in the terminal: the catalog becomes a numbered list
    lines = []
    for event in cart.take_events():
        if event['kind'] == 'catalog':
            text = catalog_text(event['catalog'])
            if not text:
                continue
            lines.append(text)
            if event['catalog']['summary']:
                lines.append('\nMe fala a marca ou até quanto quer gastar. Ex.: "yamaha" ou "até 1000".')
            else:
                lines.append('\nÉ só me falar o número. Ex.: "quero o 2" ou "2 do 5".')
        if event['kind'] == 'cart' and cart.items:
            lines.append(cart_text(cart))
            lines.append('\nEstá certo? Responda "confirmo" ou me fala o que quer mudar.')
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description='Empório da Música customer service agent')
    parser.add_argument('--debug', action='store_true', help='show which tools the agent called (never errors)')
    parser.add_argument('--save', action='store_true', help='save the conversation to conversations/')
    args = parser.parse_args()

    silence_internal_output()
    purge_old_logs()
    chat = new_chat()
    session = new_session_id()
    chat.cart.session_id = session
    history = []
    print("Empório da Música — atendimento virtual (digite 'sair' para encerrar)")
    print(f'{PRIVACY_NOTICE}\n')

    while True:
        if len(history) >= MAX_TURNS:
            print(f'Limite de mensagens desta sessão atingido. Para continuar, fale com a equipe no {STORE_PHONE}.')
            break

        try:
            message = input('Você: ').strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not message:
            continue
        if message.lower() == 'sair':
            break

        answer, calls = reply(chat, session, message)
        if args.debug:
            for call in calls:
                print(f'  [tool] {call}')
        extra = show_events(chat.cart)
        if extra:
            answer = f'{answer}\n\n{extra}'
        print(f'\nEmpório: {answer}\n')
        history.append((message, calls, answer))

    if args.save and history:
        save(history)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:  # last line of defense: no traceback on the customer's screen
        log_turn({'session': 'startup', 'error': describe_error(error)})
        print(ERROR_REPLY)
