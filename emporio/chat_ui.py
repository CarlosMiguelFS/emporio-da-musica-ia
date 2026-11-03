import time

# an instant reply feels like a robot, so the answer waits a little (with a cap)
MIN_TYPING_SECONDS = 1.2
SECONDS_PER_CHARACTER = 0.004
MAX_TYPING_SECONDS = 3.5

# the only HTML on the page: fixed CSS written here, never text from the model or the customer
TYPING_DOTS = """
<style>
.typing { display: inline-flex; gap: 5px; padding: 10px 4px; }
.typing span {
    width: 8px; height: 8px; border-radius: 50%; background: #8a8f98;
    animation: typing-bounce 1.2s infinite ease-in-out;
}
.typing span:nth-child(2) { animation-delay: 0.15s; }
.typing span:nth-child(3) { animation-delay: 0.3s; }
@keyframes typing-bounce {
    0%, 60%, 100% { transform: translateY(0); opacity: 0.5; }
    30% { transform: translateY(-6px); opacity: 1; }
}
</style>
<div class="typing"><span></span><span></span><span></span></div>
"""


def typing_time(answer):
    seconds = MIN_TYPING_SECONDS + len(answer) * SECONDS_PER_CHARACTER
    if seconds > MAX_TYPING_SECONDS:
        seconds = MAX_TYPING_SECONDS
    return seconds


def wait(seconds):
    if seconds > 0:
        time.sleep(seconds)
