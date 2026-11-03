import json
import re
import uuid
from datetime import datetime, timedelta

from emporio.settings import LOG_DIR

RETENTION_DAYS = 30  # LGPD: don't keep conversation logs longer than needed
SENSITIVE_ARGS = ['customer_name']
MAX_ERROR_MESSAGE = 300
LAST_ERRORS = 5
API_KEY_PATTERN = re.compile(r'(AIza|AQ\.)[\w.-]{20,}')  # old and new Google key formats


def new_session_id():
    return uuid.uuid4().hex[:12]


def purge_old_logs():
    # also the subfolders: team orders and chats saved with --save
    limit = datetime.now() - timedelta(days=RETENTION_DAYS)
    for file in LOG_DIR.rglob('*'):
        if file.suffix not in ['.jsonl', '.md']:
            continue
        last_change = datetime.fromtimestamp(file.stat().st_mtime)
        if last_change < limit:
            file.unlink()


def safe_args(args):
    if not args:
        return {}

    result = {}
    for name, value in args.items():
        if name in SENSITIVE_ARGS:
            result[name] = '***'
        else:
            result[name] = value
    return result


def describe_error(error):
    # the type alone doesn't say why it failed (404 retired model, 402 no credits)
    message = API_KEY_PATTERN.sub('[api-key]', str(error))
    code = None
    if hasattr(error, 'code'):
        code = error.code

    return {
        'type': type(error).__name__,
        'code': code,
        'message': message[:MAX_ERROR_MESSAGE],
    }


def log_turn(record):
    # a log that can't be written must not break the answer
    now = datetime.now()
    line = {'time': now.isoformat(timespec='seconds')}
    line.update(record)

    file_path = LOG_DIR / f'{now:%Y-%m-%d}.jsonl'
    try:
        LOG_DIR.mkdir(exist_ok=True)
        with open(file_path, 'a', encoding='utf-8') as file:
            file.write(json.dumps(line, ensure_ascii=False, default=str) + '\n')
    except OSError:
        return False
    return True


def read_logs():
    records = []
    for file in sorted(LOG_DIR.glob('*.jsonl')):
        for line in file.read_text(encoding='utf-8').splitlines():
            if line:
                records.append(json.loads(line))
    return records


def _error_label(error):
    # older log lines only had the error type as a string
    if not isinstance(error, dict):
        return error
    if error.get('code'):
        return f'{error["type"]} {error["code"]}'
    return error['type']


def _count(counter, key):
    counter[key] = counter.get(key, 0) + 1


def summary():
    records = read_logs()
    if not records:
        print('No logs yet.')
        return

    sessions = set()
    latencies = []
    total_tokens = 0
    tools = {}
    guardrail_events = {}
    errors = []
    error_kinds = {}

    for record in records:
        sessions.add(record['session'])

        if record.get('latency_ms'):
            latencies.append(record['latency_ms'])

        tokens = record.get('tokens') or {}
        total_tokens += tokens.get('total') or 0

        for tool in record.get('tools', []):
            _count(tools, tool['name'])

        for event in record.get('guardrails', []):
            event_name = event.split(':')[0]  # "input_pii_masked:[CPF removido]" -> "input_pii_masked"
            _count(guardrail_events, event_name)

        if record.get('error'):
            errors.append(record)
            _count(error_kinds, _error_label(record['error']))

    average_latency = 0
    if latencies:
        average_latency = sum(latencies) / len(latencies)
    error_rate = len(errors) / len(records)

    print(f'turns: {len(records)}  sessions: {len(sessions)}')
    print(f'avg latency: {average_latency:.0f} ms')
    print(f'tokens: {total_tokens}')
    print(f'errors: {len(errors)} ({error_rate:.0%} of turns) {error_kinds}')
    print(f'tools: {tools}')
    print(f'guardrails: {guardrail_events}')

    if errors:
        print('\nlast errors:')
        for record in errors[-LAST_ERRORS:]:
            error = record['error']
            message = ''
            if isinstance(error, dict):
                message = error.get('message', '')
            print(f'  {record["time"]}  {record.get("model")}  {_error_label(error)}  {message[:150]}')


if __name__ == '__main__':
    summary()
