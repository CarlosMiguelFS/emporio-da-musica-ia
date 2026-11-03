# Runs the cases in evals/cases.json against the real model and points out what went wrong.
#
#   python -m evals.run_evals                                 every case, 2 runs each
#   python -m evals.run_evals --runs 1 --only price_specific hours

import argparse
import json
import re
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from emporio import agent, guardrails, observability  # noqa: E402
from emporio.settings import LOG_DIR  # noqa: E402
from emporio.text_utils import escape_markdown_dollars, normalize  # noqa: E402

EVALS_DIR = Path(__file__).parent
CASES_FILE = EVALS_DIR / 'cases.json'
REPORT_FILE = EVALS_DIR / 'results' / 'latest.md'
MAX_LINES = 8
MAX_QUESTIONS = 1

# 'tudo bem?', 'né?', 'tá bom?' are just how people talk, not a question the customer has to answer
TAG_QUESTIONS = re.compile(r'(tudo bem|tudo certo|ne|viu|ta bom|ta|certo|beleza|ok)\s*(por ai|com voce)?\?')
BULLET = re.compile(r'^\s*([-*•]|\d+[.)])\s+')
PRODUCT_LINE = re.compile(r'^[^:]{3,60}: (de )?R\$')
# the store doesn't want emojis or long dashes in the answers
EMOJI = re.compile('[☀-➿\U0001f300-\U0001faff]')
DASHES = ('—', '–')


def count_options(answer):
    # "- Name: R$ 10" or just "Name: de R$ 10 por R$ 8", the model doesn't always use the dash
    options = 0
    for line in answer.splitlines():
        if 'R$' in line and (BULLET.match(line) or PRODUCT_LINE.match(line.strip())):
            options += 1
    return options


def real_questions(answer):
    text = TAG_QUESTIONS.sub('', normalize(answer))
    return text.count('?')


def count_lines(answer):
    lines = 0
    for line in answer.splitlines():
        if line.strip():
            lines += 1
    return lines


def tool_names(calls):
    # 'search_products(term=...)' -> 'search_products'
    names = []
    for call in calls:
        names.append(call.split('(')[0])
    return names


def session_logs(session):
    records = []
    for record in observability.read_logs():
        if record.get('session') == session:
            records.append(record)
    return records


def check_cart(case, cart):
    problems = []
    if 'expect_cart' in case:
        expected = {}
        for product_id, quantity in case['expect_cart'].items():
            expected[int(product_id)] = quantity
        if cart.items != expected:
            problems.append(f'cart is {cart.items}, expected {expected}')

    if case.get('expect_order') and not cart.order:
        problems.append('order was not sent to the team')
    if case.get('forbid_order') and cart.order:
        problems.append('order was sent without the customer confirming')
    return problems


def check(case, answer, tools, events):
    # returns the list of problems found in one run of a case
    problems = []
    text = normalize(answer)

    if answer == agent.ERROR_REPLY:
        problems.append('technical error (see logs/evals)')
    if answer == agent.EMPTY_REPLY:
        problems.append('empty answer twice: the customer got the fallback')
    elif 'empty_answer_retry' in events:
        problems.append('warning: empty answer, fixed by the second chance')
    if answer == guardrails.UNCONFIRMED_PRICE_REPLY:
        problems.append('price blocked twice: the customer got no price')
    elif 'price_retry' in events:
        problems.append('warning: answered a price from memory, fixed by the second chance')

    for tool in case.get('expect_tools', []):
        if tool not in tools:
            problems.append(f'did not call {tool}')

    for tool in case.get('forbid_tools', []):
        if tool in tools:
            problems.append(f"called {tool} when it shouldn't")

    # the catalog and the cart are already on the screen, a list in the text repeats them
    if 'show_catalog' in tools and count_options(answer) > 1:
        problems.append('listed products in the text and in the catalog')
    if 'show_cart' in tools and answer.count('R$') > 2:
        problems.append('listed the cart in the text and on the screen')

    one_of = case.get('expect_one_of_tools', [])
    if one_of:
        found = False
        for tool in one_of:
            if tool in tools:
                found = True
        if not found:
            problems.append(f'did not call any of {one_of}')

    for expected in case.get('must_contain', []):
        if normalize(expected) not in text:
            problems.append(f"answer is missing '{expected}'")

    for forbidden in case.get('must_not_contain', []):
        if normalize(forbidden) in text:
            problems.append(f"answer has '{forbidden}'")

    if case.get('expect_blocked') and answer != guardrails.OFF_LIMITS_REPLY:
        problems.append('injection was not blocked by the input guardrail')

    questions = real_questions(answer)
    if questions > MAX_QUESTIONS:
        problems.append(f'{questions} questions in one message')

    lines = count_lines(answer)
    if lines > case.get('max_lines', MAX_LINES):
        problems.append(f'too long: {lines} lines')

    options = count_options(answer)
    if case.get('max_options') and options > case['max_options']:
        problems.append(f'too many options listed ({options})')
    if case.get('min_options') and options < case['min_options']:
        problems.append(f'too few options listed ({options}), something the customer asked for is missing')

    if re.search(r'R\$ ?\d{4,}', answer):
        problems.append('price without thousands separator')

    if EMOJI.search(answer):
        problems.append('emoji in the answer')

    for dash in DASHES:
        if dash in answer:
            problems.append(f"long dash '{dash}' in the answer")

    if 'removido]' in answer:
        problems.append("a masked value reached the customer (e.g. the store's own contact)")

    return problems


def run_case(case, run_number, stamp):
    session = f'eval-{stamp}-{case["id"]}-{run_number}'
    chat = agent.new_chat()
    chat.cart.session_id = session
    # some cases start with products already picked on the screen, like the Streamlit buttons do
    for product_id, quantity in case.get('start_cart', {}).items():
        chat.cart.add_from_screen(int(product_id), quantity)

    # tools of the whole conversation: in a checkout the cart tools are spread over the messages
    all_calls = []
    for message in case['messages']:
        answer, calls = agent.reply(chat, session, message)
        all_calls = all_calls + calls

    records = session_logs(session)
    events = []
    latency = None
    tokens = 0
    if records:
        events = records[-1].get('guardrails', [])
        latency = records[-1].get('latency_ms')
    for record in records:
        record_tokens = record.get('tokens') or {}
        tokens += record_tokens.get('total') or 0

    tools = tool_names(calls)
    if case.get('check_all_messages'):
        tools = tool_names(all_calls)

    problems = check(case, answer, tools, events) + check_cart(case, chat.cart)
    return {
        'case': case['id'],
        'run': run_number,
        'tools': tools,
        'problems': problems,
        'answer': answer,
        'tokens': tokens,
        'latency_ms': latency,
    }


def run(cases, runs):
    results = []
    stamp = datetime.now().strftime('%H%M%S')

    for case in cases:
        case_results = []
        for run_number in range(1, runs + 1):
            result = run_case(case, run_number, stamp)
            case_results.append(result)

            status = 'OK'
            if result['problems']:
                status = '; '.join(result['problems'])
            print(f'{case["id"]:<20} run {run_number}  {status}')

        # variation: did every run call the same tools and give the same answer?
        tool_sets = []
        answers = []
        for result in case_results:
            tools = sorted(set(result['tools']))
            if tools not in tool_sets:
                tool_sets.append(tools)
            if result['answer'] not in answers:
                answers.append(result['answer'])

        if len(tool_sets) > 1:
            case_results[-1]['problems'].append(f'inconsistent tools across runs: {tool_sets}')
            print(f'{case["id"]:<20} inconsistent tools across runs: {tool_sets}')
        case_results[-1]['identical_answers'] = len(answers) == 1

        results.extend(case_results)
    return results


def write_report(results, runs):
    failed = 0
    warnings = {}
    cases = []
    identical = 0
    tokens = 0

    for result in results:
        if result['case'] not in cases:
            cases.append(result['case'])
        if result.get('identical_answers'):
            identical += 1
        tokens += result['tokens']

        has_failure = False
        for problem in result['problems']:
            if problem.startswith('warning'):
                warnings[problem] = warnings.get(problem, 0) + 1
            else:
                has_failure = True
        if has_failure:
            failed += 1

    lines = [
        f'# Eval report — {datetime.now():%d/%m/%Y %H:%M}',
        '',
        f'- cases: {len(cases)}, runs per case: {runs}, total runs: {len(results)}',
        f'- runs with problems: {failed}',
        f'- warnings: {warnings or "none"}',
        f'- cases with identical answers in every run: {identical}/{len(cases)}',
        f'- tokens: {tokens}',
        '',
        '| case | run | tools | problems |',
        '|---|---|---|---|',
    ]
    for result in results:
        tools = ', '.join(result['tools']) or '-'
        problems = '; '.join(result['problems']) or 'OK'
        lines.append(f'| {result["case"]} | {result["run"]} | {tools} | {problems} |')

    lines.append('')
    lines.append('## Answers')
    lines.append('')
    for result in results:
        answer = escape_markdown_dollars(result['answer']).replace('\n', '\n> ')
        lines.append(f'**{result["case"]} (run {result["run"]})**')
        lines.append('')
        lines.append('> ' + answer)
        lines.append('')

    REPORT_FILE.parent.mkdir(exist_ok=True)
    REPORT_FILE.write_text('\n'.join(lines), encoding='utf-8')
    print(f'\nruns with problems: {failed}/{len(results)}  ->  {REPORT_FILE}')


def main():
    parser = argparse.ArgumentParser(description='Run the agent evaluation cases')
    parser.add_argument('--runs', type=int, default=2, help='runs per case, to measure variation')
    parser.add_argument('--only', nargs='*', help='case ids to run')
    args = parser.parse_args()

    # keep eval traffic out of the customer logs
    observability.LOG_DIR = LOG_DIR / 'evals'

    cases = json.loads(CASES_FILE.read_text(encoding='utf-8'))
    if args.only:
        selected = []
        for case in cases:
            if case['id'] in args.only:
                selected.append(case)
        cases = selected

    results = run(cases, args.runs)
    write_report(results, args.runs)


if __name__ == '__main__':
    main()
