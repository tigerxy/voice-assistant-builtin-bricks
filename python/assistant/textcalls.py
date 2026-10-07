"""Tool calls the LLM writes into its answer as text, and cleaning text for speech.

The tools are registered with the LLM brick, which runs them when the model returns a
real (structured) tool call. But the small model on the board often writes the call
into its answer as plain text instead, in different styles, for example
    show_emotion("heart")      Show_emotion(heart)
    {"tool_calls": [{"type": "function", "function": {"name": "get_weather", "arguments": {...}}}]}
    <tool_call>{"name": "web_search", "arguments": {"query": "..."}}</tool_call>
The brick can't run those, so the answer is scanned for them here: they are carried
out by the Brain and never read aloud.

Everything here is plain text processing, without any hardware or brick.
"""

import json
import re

TOOL_NAMES = ("web_search", "get_weather", "show_emotion")
LOOKUP_TOOLS = ("web_search", "get_weather")

_CALL_RE = re.compile(r"\b(web_search|get_weather|show_emotion)\s*\(([^()]*)\)", re.I)
_TOOL_TAG_RE = re.compile(r"</?\s*tool_calls?\s*>", re.I)
_EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]")
_MARKDOWN_RE = re.compile(r"[*_`#~]+")


def split_speakable(buffer: str) -> tuple:
    """Split complete sentences and lines off the streamed answer: returns (pieces, rest).

    Never splits inside (...) or {...}, so a call like web_search("Mt. Everest")
    stays in one piece.
    """
    pieces, start, depth, in_string = [], 0, 0, False
    for i, c in enumerate(buffer):
        if depth and c == '"':
            in_string = not in_string
        if in_string:
            continue
        if c in "({[":
            depth += 1
        elif c in ")}]":
            depth = max(0, depth - 1)
        elif depth == 0 and c == "\n":
            pieces.append(buffer[start:i])
            start = i + 1
        elif depth == 0 and c in ".!?" and i + 1 < len(buffer) and buffer[i + 1] in " \t\n":
            pieces.append(buffer[start:i + 1])
            start = i + 1
    return [p for p in pieces if p.strip()], buffer[start:]


def extract_tool_calls(text: str) -> tuple:
    """Split a piece of the answer into (text to speak, [(tool name, args dict), ...])."""
    calls, kept, i = [], [], 0
    while i < len(text):
        if text[i] == "{":
            end = _json_end(text, i)
            if end > 0:
                try:
                    obj = json.loads(text[i:end])
                except ValueError:
                    obj = None
                if isinstance(obj, dict) and ("tool_calls" in obj or "arguments" in obj or obj.get("name") in TOOL_NAMES):
                    calls.extend(_calls_from_json(obj))
                    i = end
                    continue
        kept.append(text[i])
        i += 1

    def take_call(match):
        name = match.group(1).lower()
        calls.append((name, _args_from_call(name, match.group(2))))
        return " "

    rest = _CALL_RE.sub(take_call, "".join(kept))
    return clean_speech(rest), calls


def clean_speech(text: str) -> str:
    """Remove everything that must not be read aloud ('' if nothing speakable is left)."""
    text = _TOOL_TAG_RE.sub(" ", text)
    if "{" in text:  # an incomplete tool call, e.g. the answer was cut off
        text = text[: text.index("{")]
    text = _EMOJI_RE.sub("", text)
    text = _MARKDOWN_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text if re.search(r"\w", text) else ""


def arg(args: dict, *names, default=""):
    """First non-empty value of the given argument names (models use different names)."""
    for name in names:
        if args.get(name) not in (None, ""):
            return args[name]
    values = [v for v in args.values() if v not in (None, "")]
    return values[0] if values and default == "" else default


def _json_end(text, start):
    """Index just after the JSON object starting at text[start] ('{'), or -1 if incomplete."""
    depth, in_string, escaped = 0, False, False
    for i in range(start, len(text)):
        c = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == '"':
                in_string = False
        elif c == '"':
            in_string = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i + 1
    return -1


def _calls_from_json(obj):
    calls = obj["tool_calls"] if isinstance(obj.get("tool_calls"), list) else [obj]
    found = []
    for call in calls:
        if not isinstance(call, dict):
            continue
        fn = call.get("function") if isinstance(call.get("function"), dict) else call
        name = str(fn.get("name", "")).lower()
        args = fn.get("arguments", fn.get("parameters", {}))
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {"text": args}
        if name in TOOL_NAMES:
            found.append((name, args if isinstance(args, dict) else {}))
    return found


def _args_from_call(name, arg_text):
    """Arguments of a written call like get_weather("Berlin", 1) or get_weather(city="Berlin")."""
    keywords = {
        k.lower(): v.strip().strip("'\"")
        for k, v in re.findall(r"(\w+)\s*=\s*(\"[^\"]*\"|'[^']*'|[^,]+)", arg_text)
    }
    positional = [p.strip().strip("'\"") for p in arg_text.split(",") if p.strip() and "=" not in p]
    args = dict(keywords)
    if name == "web_search" and positional:
        args.setdefault("query", ", ".join(positional))
    elif name == "show_emotion" and positional:
        args.setdefault("emotion", positional[0])
    elif name == "get_weather":
        numbers = [p for p in positional if re.fullmatch(r"-?\d+", p)]
        words = [p for p in positional if p not in numbers]
        if words:
            args.setdefault("city", words[0])
        if numbers:
            args.setdefault("days_ahead", numbers[0])
    return args
