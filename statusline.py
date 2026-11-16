# ━━━━ CLAUDE CODE STATUS LINE ━━━━
#
# https://github.com/Mmathieus/claude-code-statusline
#
# Written with Claude Opus 5.5 in Claude Code.
#
#     Opus 5.5 · xhigh │ 120K █░░░░░░░░░ │ 5h 37% │ 7d 13% │  main
#
# Segments (left to right)
#   - Model and reasoning effort
#   - Context window: tokens in use and a bar that goes from green through amber to red
#   - 5-hour and 7-day usage limits, plus the time left until reset once a limit reaches 70 % (set apart for each limit or always)
#   - Git branch of the working directory (short commit hash when detached)
# A segment without data is left out. A segment that fails shows "<name>: err" instead, so one problem never blanks the whole line.
#
# Colors mark state only (context, limits), plus the model name in Claude orange.
#
# Requirements
#   - Python 3.11 or newer, standard library only (nothing to install)
#   - A terminal with 24-bit color and a Nerd Font (for the branch and timer icons)
#
# Setup
#   1. Save this file as ~/.claude/statusline.py
#   2. Add to ~/.claude/settings.json:
#        "statusLine": {
#          "type": "command",
#          "command": "python -I -S -X utf8 -c \"import os, sys; sys.path.append(os.path.expanduser('~/.claude')); import statusline; statusline.main()\"",
#          "refreshInterval": 10
#        }
#      Where Python is installed as python3 (macOS, most Linux), write python3 instead.
#
# How it works
#   - Claude Code pipes the session as JSON to stdin and shows what the script prints
#   - Claude Code runs the command after every event and every refreshInterval seconds
#   - Fields read:
#       - model.display_name
#       - effort.level
#       - context_window.{total_input_tokens, used_percentage}
#       - rate_limits.{five_hour, seven_day}.{used_percentage, resets_at}
#       - workspace.current_dir
#   - The JSON has no Git branch, so it is read from .git/HEAD
#   - Official docs on status lines: https://code.claude.com/docs/en/statusline
#
# Why the command is so long
#   Python starts anew on every update, so startup is most of the cost.
#   - Importing the file instead of running it reuses the bytecode cached in __pycache__
#   - -I       Ignores PYTHON* environment variables and the user's site-packages
#   - -S       Skips site-packages altogether, hence the standard library only
#   - -X utf8  Saves loading the Windows ANSI codec
#   On Windows a run takes about 70 ms, half of it the Git Bash that Claude Code starts commands with.
#
# Limitations
#   - A new session shows 0K (zero tokens) until the first response, as Claude Code has no usage data before then
#   - The usage limits arrive a moment after startup but show only on the next update, which is why refreshInterval is short
#   - Usage limits are reported only for Claude Pro and Max subscriptions
#   - In Git repositories that use the reftable format, .git/HEAD does not name the branch, so the git command is run (~70 ms on Windows)

import os
import sys
import time
from itertools import pairwise

# ━━━━ SETTINGS ━━━━

BAR_WIDTH = 10  # cells in the context bar
BAR_FILLED, BAR_EMPTY = "█", "░"
# Show the time until reset once a usage limit reaches this %; 0 shows it always.
RESET_TIME_FROM_5H = 70
RESET_TIME_FROM_7D = 70

MODEL_RGB = (215, 119, 87)  # Claude orange

# The gradient keeps one OKLCH lightness, so every step has about 4:1 contrast on dark and light backgrounds; only the hue changes.
LIGHTNESS, CHROMA = 0.62, 0.15
HUE_STOPS = ((0, 150), (60, 80), (100, 28))  # (fill %, hue): green, amber, red

# Nerd Font icons
TIMER_ICON = "\U000f051b"  # nf-md-timer_outline
BRANCH_ICON = "\uf418"  # nf-oct-git_branch

# ━━━━ TERMINAL STYLING ━━━━

RGB = tuple[int, int, int]
ANSI_RESET = "\x1b[0m"


def paint(rgb: RGB, text: str) -> str:
    return f"\x1b[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m{text}{ANSI_RESET}"


def dim(text: str) -> str:
    return f"\x1b[2m{text}{ANSI_RESET}"


# ━━━━ NUMBERS AND TEXT ━━━━


# Python's round() rounds halves to even (36.5 -> 36), but the line should show 37.
def round_half_up(x: float) -> int:
    return int(x + 0.5)


def format_tokens(count: int) -> str:
    if count >= 999_500:  # would otherwise round to "1000K"
        return f"{count / 1_000_000:.1f}M"
    return f"{round_half_up(count / 1000)}K"


# Time left as "2d5h", "3h07m" or "12m"; None once it has passed.
def format_time_left(reset_at: float) -> str | None:
    seconds = int(reset_at - time.time())
    if seconds <= 0:
        return None
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    if days:
        return f"{days}d{hours}h"
    if hours:
        return f"{hours}h{seconds // 60:02d}m"
    return f"{max(1, seconds // 60)}m"


# ━━━━ COLORS ━━━━


# OKLCH to 8-bit sRGB with Björn Ottosson's OKLab matrices. Channels outside the sRGB gamut are clipped.
def oklch_to_rgb(lightness: float, chroma: float, hue: float) -> RGB:
    # The hue's cos and sin via e^(i*hue) = (-1) ** (hue / 180), so the math module need not be imported.
    hue_vector = (-1) ** (hue / 180)
    a, b = chroma * hue_vector.real, chroma * hue_vector.imag
    cone_l = (lightness + 0.3963377774 * a + 0.2158037573 * b) ** 3
    cone_m = (lightness - 0.1055613458 * a - 0.0638541728 * b) ** 3
    cone_s = (lightness - 0.0894841775 * a - 1.2914855480 * b) ** 3
    linear = (
        4.0767416621 * cone_l - 3.3077115913 * cone_m + 0.2309699292 * cone_s,
        -1.2684380046 * cone_l + 2.6097574011 * cone_m - 0.3413193965 * cone_s,
        -0.0041960863 * cone_l - 0.7034186147 * cone_m + 1.7076147010 * cone_s,
    )

    def gamma_encode(x: float) -> float:
        x = max(0.0, min(1.0, x))
        return 12.92 * x if x <= 0.0031308 else 1.055 * x ** (1 / 2.4) - 0.055

    red, green, blue = (round_half_up(gamma_encode(v) * 255) for v in linear)
    return red, green, blue


# Color for a fill level of 0-100 %, with the hue interpolated between HUE_STOPS.
def fill_color(percent: float) -> RGB:
    percent = max(0.0, min(100.0, percent))
    for (start, start_hue), (end, end_hue) in pairwise(HUE_STOPS):
        if percent <= end:
            hue = start_hue + (end_hue - start_hue) * (percent - start) / (end - start)
            return oklch_to_rgb(LIGHTNESS, CHROMA, hue)
    return oklch_to_rgb(LIGHTNESS, CHROMA, HUE_STOPS[-1][1])


# Each filled cell takes the color of its own position, so the tip shows the level.
def context_bar(percent: float) -> str:
    filled = min(BAR_WIDTH, round_half_up(percent / 100 * BAR_WIDTH))
    cells = "".join(paint(fill_color((i + 0.5) / BAR_WIDTH * 100), BAR_FILLED) for i in range(filled))
    return cells + dim(BAR_EMPTY * (BAR_WIDTH - filled))


# ━━━━ GIT ━━━━


# Reads the branch from .git/HEAD, since running git costs ~70 ms per run on Windows:
#   - Follows the "gitdir:" file that worktrees and submodules have instead of a .git folder
#   - A detached HEAD gives the short commit hash
#   - Reftable repositories keep only a placeholder in HEAD, so for them git is run after all
def git_branch(directory: str) -> str | None:
    path = os.path.abspath(directory)
    while not os.path.exists(os.path.join(path, ".git")):
        parent = os.path.dirname(path)
        if parent == path:  # reached the root without finding a repository
            return None
        path = parent
    git_dir = os.path.join(path, ".git")
    if os.path.isfile(git_dir):
        with open(git_dir, encoding="utf-8") as f:
            pointer = f.read().strip()
        if not pointer.startswith("gitdir:"):
            return None
        git_dir = os.path.join(path, pointer.removeprefix("gitdir:").strip())
    with open(os.path.join(git_dir, "HEAD"), encoding="utf-8") as f:
        head = f.read().strip()
    if head == "ref: refs/heads/.invalid":
        return ask_git_for_branch(path)
    if head.startswith("ref: "):
        return head.removeprefix("ref: ").removeprefix("refs/heads/")
    return head[:7] or None


# Asks git itself: symbolic-ref names the branch, and when HEAD is detached, rev-parse gives the short hash.
def ask_git_for_branch(repo: str) -> str | None:
    import subprocess  # imported here, so only reftable repositories pay for it

    for args in (("symbolic-ref", "--short", "-q", "HEAD"), ("rev-parse", "--short=7", "HEAD")):
        result = subprocess.run(("git", "-C", repo, *args), capture_output=True, encoding="utf-8", errors="replace", check=False)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    return None


# ━━━━ SEGMENTS ━━━━
# Each takes the session JSON and returns its parts of the line, or none when its data is missing.


def model_segment(session: dict) -> list[str]:
    name = (session.get("model") or {}).get("display_name")
    if not name:
        return []
    base, paren, _ = name.partition("(")
    if paren and name.endswith(")"):  # "Opus 5.5 (1M context)" -> "Opus 5.5"
        name = base.rstrip()
    text = paint(MODEL_RGB, name)
    effort = (session.get("effort") or {}).get("level")  # absent if the model has no effort levels
    if effort:
        text += dim(" · ") + effort
    return [text]


def context_segment(session: dict) -> list[str]:
    context = session.get("context_window") or {}
    used_tokens = context.get("total_input_tokens")
    if used_tokens is None:
        return []
    percent = context.get("used_percentage") or 0  # null only while no tokens are used
    tokens = paint(fill_color(percent), format_tokens(used_tokens))
    return [f"{tokens} {context_bar(percent)}"]


def limit_segments(session: dict) -> list[str]:
    segments = []
    limits = session.get("rate_limits") or {}
    for key, label, show_from in (("five_hour", "5h", RESET_TIME_FROM_5H), ("seven_day", "7d", RESET_TIME_FROM_7D)):
        limit = limits.get(key) or {}
        if limit.get("used_percentage") is None:
            continue
        percent = round_half_up(limit["used_percentage"])  # compare the number shown
        text = dim(label) + " " + paint(fill_color(percent), f"{percent}%")
        if percent >= show_from and limit.get("resets_at"):
            time_left = format_time_left(limit["resets_at"])
            if time_left:
                text += " " + dim(f"{TIMER_ICON} {time_left}")
        segments.append(text)
    return segments


def branch_segment(session: dict) -> list[str]:
    directory = (session.get("workspace") or {}).get("current_dir") or session.get("cwd")
    branch = git_branch(directory) if directory else None
    return [dim(BRANCH_ICON) + " " + branch] if branch else []


SEGMENTS = (
    ("model", model_segment),
    ("context", context_segment),
    ("limits", limit_segments),
    ("branch", branch_segment),
)


def render_line(session: dict) -> str:
    parts = []
    for name, build in SEGMENTS:
        try:
            parts += build(session)
        except Exception:  # one broken segment must not blank the whole line
            parts.append(dim(f"{name}: err"))
    return dim(" │ ").join(parts)


# ━━━━ JSON INPUT ━━━━
# Importing json would add ~18 ms per run, as it compiles the regexes of its pure-Python fallback and so pulls in re and enum.
# The C scanner behind json.loads, in the private _json module, parses the same without them:
#   - If _json is missing or has changed, the public json module takes over
#   - The checks after scanning match json.loads, so both accept the same input


# The decoder settings make_scanner reads, at their json.loads defaults.
class JsonLoadsDefaults:
    strict = True
    object_hook = object_pairs_hook = None
    parse_float, parse_int, parse_constant = float, int, float


def parse_json(raw: bytes) -> object:
    try:
        from _json import make_scanner

        scan = make_scanner(JsonLoadsDefaults())
    except Exception:
        import json

        return json.loads(raw)
    # The BOM is stripped by hand, as the utf-8-sig codec would be one more import.
    text = raw.removeprefix(b"\xef\xbb\xbf").decode().strip(" \t\n\r")
    try:
        value, end = scan(text, 0)
    except StopIteration:
        raise ValueError("expected a JSON value") from None
    if end != len(text):
        raise ValueError("extra data after the JSON value")
    return value


# ━━━━ MAIN ━━━━


def main() -> None:
    try:
        session = parse_json(sys.stdin.buffer.read() or b"{}")
        if not isinstance(session, dict):
            raise TypeError("expected a JSON object")
        line = render_line(session)
    except Exception as error:  # unreadable input: say so rather than show nothing
        line = dim(f"statusline: {type(error).__name__}: {error}")
    # Bytes, so the icons survive even without -X utf8: Windows pipes default to the ANSI code page, which lacks them.
    sys.stdout.buffer.write(line.encode("utf-8"))


if __name__ == "__main__":
    main()
