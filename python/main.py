import json
import queue
import random
import re
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from requests.exceptions import ConnectionError, ReadTimeout

from arduino.app_utils import App, Bridge
from arduino.app_bricks.llm import LargeLanguageModel
from arduino.app_bricks.asr import AutomaticSpeechRecognition
from arduino.app_bricks.keyword_spotting import KeywordSpotting
from arduino.app_bricks.tts import TextToSpeech
from arduino.app_bricks.weather_forecast import WeatherForecast
from arduino.app_bricks.sound_generator import SoundGenerator, SoundEffect
from arduino.app_peripherals.microphone import Microphone

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
TIME_ZONE = ZoneInfo("Europe/Berlin")  # your IANA time zone
COMMAND_SECONDS = 7        # how long to listen right after the wake word
FOLLOW_UP_SECONDS = 6      # how long to wait for a reply before ending the conversation
MEMORY_MESSAGES = 16       # conversation history kept for the LLM
USE_TOOLS = True           # let the LLM search the web, check the weather and show emotions
MAX_LOOKUP_ROUNDS = 2      # how often the LLM may look something up before it has to answer
# Language you speak to the assistant and it answers in. The default TTS voice only
# speaks English: to change it, also set a matching TTS model in app.yaml.
ASR_LANGUAGE = "en"        # None = detect automatically (can mistake English for German)
REPLY_LANGUAGE = "English"
SEARCH_TIMEOUT = 8         # seconds per web request
SEARCH_LANG = "en"         # Wikipedia language edition used by web_search
SOUND_EFFECTS = True       # short chimes and jingles via the sound_generator brick
SOUND_VOLUME = 0.35        # 0.0 .. 1.0
THINKING_FILLER_SECONDS = 3.0  # say "hmm..." if the first words take longer than this
IDLE_FACE = "auto"         # "auto" (falls asleep after inactivity), "awake" or "off"
SLEEP_AFTER_SECONDS = 5 * 60  # show the sleeping face after this long without a conversation
NIGHT_HOURS = (22, 7)      # used for wording only, e.g. "Good night!" from 22:00 to 07:00

# Saying one of these ends the conversation
EXIT_PHRASES = ("stop", "goodbye", "bye", "that's all", "that is all", "thank you, that's it", "never mind")

# Bridge states understood by sketch.ino
IDLE, LISTENING, PROCESSING, SPEAKING = 0, 1, 2, 3
IDLE_AWAKE, IDLE_SLEEPING, IDLE_OFF = 0, 1, 2

# Emotion symbols on the LED matrix. Order must match EMOTION_BITMAPS in sketch.ino
EMOTIONS = ["heart", "happy", "sad", "surprised", "wink", "angry", "confused", "star"]
EMOTION_HOLD_SECONDS = 2.0  # keep the symbol visible a moment after speaking

# Things a human would say while busy or in small talk. Picked at random for variety.
THINKING_FILLERS = ["Hmm.", "Hmm, let me think.", "Good question.", "Let me see.", "Okay, one second."]
SEARCH_FILLERS = ["Let me look that up.", "One moment, I'll check.", "Hmm, let me search for that.", "Let me find out."]
WEATHER_FILLERS = ["Let me check the weather.", "One moment, I'll look at the forecast."]
DIDNT_CATCH = ["Sorry, I didn't catch that.", "Hm? I didn't quite hear you.", "Sorry, could you say that again later?"]
THANKS_REPLIES = ["You're welcome!", "Anytime!", "Happy to help!", "My pleasure!", "No problem!"]
ERROR_REPLIES = ["Oops, something went wrong on my side. Let's try that again later.",
                 "Sorry, my head is a bit foggy right now. Please try again in a moment."]

THANKS_RE = re.compile(r"^(ok(ay)?,? )?(thanks|thank you|thx|cheers)( (so|very) much| a lot)?( arduino)?$")
GREETING_RE = re.compile(r"^(hi|hello|hey|good (morning|afternoon|evening))( there| arduino)?$")

# Short sound effects: (note, seconds). "REST" is a pause.
EARCONS = {
    "wake":      [("E5", 0.07), ("A5", 0.12)],
    "end":       [("A5", 0.07), ("E5", 0.12)],
    "error":     [("C4", 0.15), ("REST", 0.05), ("C4", 0.2)],
    # one jingle per emotion, played together with the symbol on the LED matrix
    "heart":     [("C5", 0.09), ("E5", 0.09), ("G5", 0.09), ("C6", 0.25)],
    "happy":     [("G5", 0.08), ("C6", 0.15)],
    "sad":       [("G4", 0.2), ("E4", 0.2), ("C4", 0.35)],
    "surprised": [("C5", 0.05), ("G5", 0.05), ("C6", 0.15)],
    "wink":      [("E6", 0.06), ("C6", 0.1)],
    "angry":     [("C3", 0.12), ("REST", 0.04), ("B2", 0.25)],
    "confused":  [("E5", 0.12), ("D5", 0.12), ("F5", 0.2)],
    "star":      [("C6", 0.06), ("E6", 0.06), ("G6", 0.06), ("C7", 0.2)],
}

SYSTEM_PROMPT = (
    "You are a friendly voice assistant having a spoken conversation. "
    "Sound natural and warm, like a person: vary how you start your sentences, and feel free to use "
    "small interjections such as oh, hmm or well. Don't greet the user unless they greet you. "
    "Keep each answer brief and conversational, at most two or three sentences. "
    f"Always answer in {REPLY_LANGUAGE}. Everything you write is read aloud: write numbers, symbols and units "
    "as words, for example 86% becomes 86 percent, and never use emojis, markdown, lists or code. "
    "The conversation continues after your answer, so you may ask a short follow-up question when it helps."
)

TOOLS_PROMPT = (
    "\n\nYou have three tools. "
    "Use web_search to look up facts you are not sure about: people, places, things, or anything that may "
    "have changed recently. Use get_weather for weather questions. You get their result before you answer. "
    "Use show_emotion at most once per answer, when it has a clear feeling, to show a symbol on your LED face: "
    "heart for affection, happy for joy, sad for bad news, surprised for amazing facts, wink for jokes, "
    "confused when you don't understand, star for praise. Never mention the symbol or the tools in your answer. "
    "Don't use tools for small talk or things you know well."
)
if USE_TOOLS:
    SYSTEM_PROMPT += TOOLS_PROMPT

# ---------------------------------------------------------------------------
# Small human touches: time of day, fillers, sounds
# ---------------------------------------------------------------------------
def now_local():
    return datetime.now(TIME_ZONE)


def part_of_day(hour=None):
    hour = now_local().hour if hour is None else hour
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 18:
        return "afternoon"
    if 18 <= hour < 22:
        return "evening"
    return "night"


def is_night(hour=None):
    hour = now_local().hour if hour is None else hour
    start, end = NIGHT_HOURS
    return hour >= start or hour < end


def normalize(text):
    return re.sub(r"[^a-z' ]", "", text.lower()).strip()


def greeting_reply():
    part = part_of_day()
    if part == "night":
        return random.choice(["Hi! You're up late. What's on your mind?", "Hey there, night owl! What can I do for you?"])
    return random.choice([f"Good {part}! What can I do for you?", f"Hi! Nice to hear from you this {part}. What's up?", "Hey! How can I help?"])


def farewell():
    if is_night():
        return random.choice(["Good night, sleep well!", "Okay, good night!"])
    return random.choice(["Okay, talk to you later!", "Bye! Just call me if you need me.", f"See you! Have a nice {part_of_day()}."])


sfx = None  # SoundGenerator, created below


def play_earcon(name, block=False):
    """Play a short sound effect from EARCONS. Never lets a sound problem break the conversation."""
    if not (SOUND_EFFECTS and sfx and name in EARCONS):
        return
    try:
        for note, seconds in EARCONS[name]:
            sfx.play_tone(note, seconds, volume=SOUND_VOLUME, block=block)
    except Exception as e:
        print(f"⚠️ Sound effect '{name}' failed: {e}")


# Set as soon as the assistant makes any sound in the current turn (filler or answer)
turn_has_spoken = threading.Event()


def say_filler(options):
    """Say a short filler phrase without blocking (it's queued before the answer)."""
    turn_has_spoken.set()
    Bridge.call("set_state", SPEAKING)
    voice.say(random.choice(options))


class Voice:
    """Speaks queued sentences one after another in a background thread.

    Lets the assistant start talking while the LLM is still generating. Only uses
    TextToSpeech.speak(text) (blocks until spoken), which every version of the TTS
    brick has, so it also works with the older library on the board.
    """

    def __init__(self, tts_brick):
        self._tts = tts_brick
        self._queue = queue.Queue()
        self._pending = 0
        self._generation = 0  # bumped by stop_talking() to drop queued sentences
        self._done = threading.Condition()
        threading.Thread(target=self._worker, name="Voice", daemon=True).start()

    def say(self, text, wait=False):
        """Queue text to be spoken. With wait=True, return only when everything is spoken."""
        text = text.strip()
        if text:
            with self._done:
                self._pending += 1
                generation = self._generation
            self._queue.put((generation, text))
        if wait:
            self.wait()

    def is_busy(self):
        with self._done:
            return self._pending > 0

    def wait(self):
        """Block until everything queued has been spoken."""
        with self._done:
            self._done.wait_for(lambda: self._pending == 0)

    def stop_talking(self):
        """Drop everything still queued and interrupt the current sentence."""
        with self._done:
            self._generation += 1
        try:
            self._tts.cancel()
        except Exception as e:
            print(f"⚠️ Could not cancel speech: {e}")

    def _worker(self):
        while True:
            generation, text = self._queue.get()
            try:
                if generation == self._generation:
                    self._tts.speak(text)
            except Exception as e:
                print(f"⚠️ Speech failed: {e}")
            finally:
                with self._done:
                    self._pending -= 1
                    self._done.notify_all()


# ---------------------------------------------------------------------------
# Tools the LLM can call
# ---------------------------------------------------------------------------
_http = requests.Session()
_http.headers["User-Agent"] = "ArduinoVoiceAssistant/1.0 (offline voice assistant demo)"
_weather = WeatherForecast()


def _wikipedia(query: str) -> str:
    base = f"https://{SEARCH_LANG}.wikipedia.org"
    r = _http.get(
        f"{base}/w/api.php",
        params={"action": "query", "list": "search", "srsearch": query, "srlimit": 3, "format": "json"},
        timeout=SEARCH_TIMEOUT,
    )
    r.raise_for_status()
    hits = r.json().get("query", {}).get("search", [])
    results = []
    for hit in hits[:2]:
        title = hit["title"]
        s = _http.get(f"{base}/api/rest_v1/page/summary/{requests.utils.quote(title)}", timeout=SEARCH_TIMEOUT)
        if s.ok and s.json().get("extract"):
            results.append(f"{title}: {s.json()['extract']}")
    return "\n\n".join(results)


def _duckduckgo(query: str) -> str:
    r = _http.get(
        "https://api.duckduckgo.com/",
        params={"q": query, "format": "json", "no_html": 1, "skip_disambig": 1},
        timeout=SEARCH_TIMEOUT,
    )
    r.raise_for_status()
    data = r.json()
    parts = [data.get("Answer"), data.get("AbstractText")]
    parts += [t.get("Text") for t in data.get("RelatedTopics", [])[:3] if isinstance(t, dict)]
    return "\n".join(p for p in parts if p)


def web_search(query: str) -> str:
    """Search the internet for facts and background information.

    Args:
        query: A short search query with the key words, for example "Eiffel Tower height".

    Returns:
        Text snippets from the search results.
    """
    print(f"\n🔎 Searching the web for: {query}")
    if not turn_has_spoken.is_set():
        say_filler(SEARCH_FILLERS)
    found = []
    for source in (_duckduckgo, _wikipedia):
        try:
            text = source(query)
            if text:
                found.append(text)
        except Exception as e:
            print(f"⚠️ Search source failed: {e}")
    if not found:
        return "No results found."
    return "\n\n".join(found)[:2000]  # keep the context small for the local model


def get_weather(city: str, days_ahead: int = 0) -> str:
    """Get the weather forecast for a city.

    Args:
        city: City name, for example "Berlin".
        days_ahead: 0 for today, 1 for tomorrow, up to 6.

    Returns:
        A short description of the expected weather.
    """
    try:
        days_ahead = max(0, min(int(days_ahead), 6))
    except (TypeError, ValueError):
        days_ahead = 0
    print(f"\n🌦️ Getting weather for {city} (+{days_ahead} days)")
    if not turn_has_spoken.is_set():
        say_filler(WEATHER_FILLERS)
    try:
        data = _weather.get_forecast_by_city(city, timezone=str(TIME_ZONE), forecast_days=days_ahead + 1)
        return f"Weather in {city}: {data.description} ({data.category})."
    except Exception as e:
        return f"Weather lookup failed: {e}"


emotion_shown = False


def display_emotion(name):
    """Show an emotion symbol on the LED matrix and play its jingle."""
    Bridge.call("show_emotion", EMOTIONS.index(name))
    play_earcon(name)


def show_emotion(emotion: str) -> str:
    """Show an emotion symbol on the assistant's LED matrix face while it answers.

    Args:
        emotion: One of "heart", "happy", "sad", "surprised", "wink", "angry", "confused", "star".

    Returns:
        Confirmation that the symbol is shown.
    """
    global emotion_shown
    name = emotion.strip().lower()
    if name not in EMOTIONS:
        return f"Unknown emotion '{emotion}'. Use one of: {', '.join(EMOTIONS)}."
    print(f"\n😀 Showing emotion: {name}")
    display_emotion(name)
    emotion_shown = True
    return f"The {name} symbol is now shown. Now give your spoken answer."


# The tools handed to the LLM brick (tools=...). Plain functions are fine: the brick
# wraps each one into a LangChain tool, using its name, type hints and docstring.
TOOLS = [web_search, get_weather, show_emotion]


# ---------------------------------------------------------------------------
# Fallback: tool calls the LLM writes into its answer as text
# ---------------------------------------------------------------------------
# The tools are registered with the LLM brick, which runs them when the model returns
# a real (structured) tool call. But the local model on the board often writes the
# call into its answer as plain text instead, in different styles, for example
#   show_emotion("heart")      Show_emotion(heart)
#   {"tool_calls": [{"type": "function", "function": {"name": "get_weather", "arguments": {...}}}]}
#   <tool_call>{"name": "web_search", "arguments": {"query": "..."}}</tool_call>
# The brick can't run those, so the answer is also scanned for them: they are carried
# out here and never read aloud.
ACTION_NAMES = ("web_search", "get_weather", "show_emotion")
LOOKUP_ACTIONS = ("web_search", "get_weather")
CALL_RE = re.compile(r"\b(web_search|get_weather|show_emotion)\s*\(([^()]*)\)", re.I)
TOOL_TAG_RE = re.compile(r"</?\s*tool_calls?\s*>", re.I)
EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200D]")
MARKDOWN_RE = re.compile(r"[*_`#~]+")


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


def _actions_from_json(obj):
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
        if name in ACTION_NAMES:
            found.append((name, args if isinstance(args, dict) else {}))
    return found


def _actions_from_call(name, arg_text):
    """Arguments of a written call like get_weather("Berlin", 1) or get_weather(city="Berlin")."""
    keywords = {k.lower(): v.strip().strip("'\"") for k, v in re.findall(r"(\w+)\s*=\s*(\"[^\"]*\"|'[^']*'|[^,]+)", arg_text)}
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


def clean_speech(text):
    """Remove everything that must not be read aloud."""
    text = TOOL_TAG_RE.sub(" ", text)
    if "{" in text:  # an incomplete action, e.g. the answer was cut off
        text = text[: text.index("{")]
    text = EMOJI_RE.sub("", text)
    text = MARKDOWN_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text if re.search(r"\w", text) else ""


def extract_actions(text):
    """Split a piece of the answer into (text to speak, [(action, args), ...])."""
    actions, kept, i = [], [], 0
    while i < len(text):
        if text[i] == "{":
            end = _json_end(text, i)
            if end > 0:
                blob = text[i:end]
                try:
                    obj = json.loads(blob)
                except ValueError:
                    obj = None
                if isinstance(obj, dict) and ("tool_calls" in obj or "arguments" in obj or obj.get("name") in ACTION_NAMES):
                    actions.extend(_actions_from_json(obj))
                    i = end
                    continue
        kept.append(text[i])
        i += 1

    def take_call(match):
        name = match.group(1).lower()
        actions.append((name, _actions_from_call(name, match.group(2))))
        return " "

    rest = CALL_RE.sub(take_call, "".join(kept))
    return clean_speech(rest), actions


def split_speakable(buffer):
    """Split complete sentences and lines off the streamed answer: returns (pieces, rest).

    Never splits inside (...) or {...}, so an action like web_search("Mt. Everest")
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


def _arg(args, *names, default=""):
    for name in names:
        if args.get(name) not in (None, ""):
            return args[name]
    values = [v for v in args.values() if v not in (None, "")]
    return values[0] if values and default == "" else default


def run_lookups(lookups):
    """Carry out web_search / get_weather written as text; return the results as the next message."""
    results = []
    for name, args in lookups[:3]:
        if name == "web_search":
            query = str(_arg(args, "query", "q", "text"))
            results.append(f'web_search("{query}"):\n{web_search(query)}')
        else:
            city = str(_arg(args, "city", "location", "place"))
            days = _arg(args, "days_ahead", "days", "day", default=0)
            results.append(f'get_weather("{city}", {days}):\n{get_weather(city, days)}')
    return (
        "[Results of your actions]\n" + "\n\n".join(results) + "\n\n"
        "Now answer my question in one or two short spoken sentences, using these results. "
        "Do not write web_search or get_weather again."
    )


# ---------------------------------------------------------------------------
# Bricks
# ---------------------------------------------------------------------------
print("=" * 50)
print("🚀 PHASE 1: Loading LOCAL LLM into RAM...")
print("=" * 50)

llm = LargeLanguageModel(
    system_prompt=SYSTEM_PROMPT,
    tools=TOOLS if USE_TOOLS else None,
)
llm.with_memory(MEMORY_MESSAGES)

try:
    print("⏳ Moving model to NPU. Please wait a moment...")
    llm.chat("Respond 'ok'")
    llm.clear_memory()  # don't keep the warm-up in the conversation
    print("✅ Local LLM loaded.")
except Exception as e:
    print(f"❌ Critical error loading LLM: {e}")

print("\n" + "=" * 50)
print("🎙️ INITIALIZING AUDIO & TTS")
print("=" * 50)

# Built-in TTS brick. With no arguments it plays on the first plugged speaker.
# To force a specific output (the original used ALSA card 1, device 0):
#   from arduino.app_peripherals.speaker import Speaker
#   tts = TextToSpeech(speaker=Speaker("plughw:1,0", sample_rate=Speaker.RATE_44K, shared=True))
# (Bricks are started by App.run() below; starting them here as well would
# warm up the TTS and ASR models twice and slow down the start.)
tts = TextToSpeech()
voice = Voice(tts)

if SOUND_EFFECTS:
    try:
        # Shares the speaker with TTS (both open it in shared mode)
        sfx = SoundGenerator(wave_form="sine", sound_effects=[SoundEffect.adsr()])
    except Exception as e:
        print(f"⚠️ Sound effects disabled: {e}")
        sfx = None

# Microphone for speech recognition. It is only switched on while the assistant
# listens, so it never records the assistant's own voice.
# The wake word detector opens its own stream of the same physical microphone
# (ALSA shared mode). The two must not read from one Microphone object: each
# read takes the chunk away from the other, so both would get half the audio.
mic = Microphone()
asr = AutomaticSpeechRecognition(mic, language=ASR_LANGUAGE)

# IDLE -> LISTENING -> PROCESSING -> FOLLOW_UP -> PROCESSING -> ... -> IDLE
app_state = "IDLE"
user_text = ""


def on_keyword_detected():
    global app_state
    if app_state == "IDLE":
        print("\n✨ Wake word 'Hey Arduino' detected!")
        app_state = "LISTENING"


spotter = KeywordSpotting(confidence=0.90, debounce_sec=2.0)  # own mic stream, at the model's sample rate
# The built-in keyword spotting model only knows "hey_arduino"
spotter.on_detect("hey_arduino", on_keyword_detected)


current_idle_mode = None
last_activity = time.monotonic()  # end of the last conversation (or app start)


def update_idle_face():
    """Awake eyes after a conversation, sleeping face after SLEEP_AFTER_SECONDS of inactivity."""
    global current_idle_mode
    if IDLE_FACE == "off":
        mode = IDLE_OFF
    elif IDLE_FACE == "awake":
        mode = IDLE_AWAKE
    else:
        idle_for = time.monotonic() - last_activity
        mode = IDLE_SLEEPING if idle_for >= SLEEP_AFTER_SECONDS else IDLE_AWAKE
    if mode != current_idle_mode:
        Bridge.call("set_idle_mode", mode)
        current_idle_mode = mode


update_idle_face()
Bridge.call("set_state", IDLE)
print("\n✅ All systems online! 💤 Listening for 'Hey Arduino'...")


# ---------------------------------------------------------------------------
# Conversation
# ---------------------------------------------------------------------------
def end_conversation(message, sound="end"):
    global app_state, last_activity
    print(message)
    if sound:
        play_earcon(sound, block=True)
    llm.clear_memory()  # the next "Hey Arduino" starts a fresh conversation
    last_activity = time.monotonic()  # wide awake again; the sleep countdown restarts
    update_idle_face()
    Bridge.call("set_state", IDLE)
    app_state = "IDLE"
    print("💤 Listening for 'Hey Arduino'...")


def listen(seconds):
    """Record and transcribe one utterance. Returns the text ('' if nothing heard)."""
    text = ""
    partial = ""

    mic.start()
    time.sleep(0.1)
    Bridge.call("set_state", LISTENING)
    print(f"\n🟢 Listening ({seconds}s)...")

    try:
        with asr.transcribe_stream(duration=seconds) as stream:
            for chunk in stream:
                match chunk.type:
                    case "partial_text":
                        print(f"\r👂 {chunk.data}", end="", flush=True)
                        partial = chunk.data.strip()
                    case "full_text":
                        text = chunk.data.strip()
                        break
    except (ReadTimeout, ConnectionError) as e:
        print(f"\n⚠️ Container connection timeout: {e}")
    except RuntimeError as e:
        print(f"\n⚠️ ASR container error: {e}")
    except Exception as e:
        print(f"\n⚠️ Unexpected ASR error: {e}")

    time.sleep(1.5)  # let ASR release its resources
    mic.stop()  # closed while the assistant talks, so it never hears itself
    return text or partial


def wants_to_stop(text):
    t = text.lower().strip(" .!?,")
    return any(t == p or t.startswith(p + " ") or t.endswith(" " + p) for p in EXIT_PHRASES)


def answer_round(prompt):
    """Stream one LLM reply: speak its sentences, carry out emotions and collect lookups."""
    lookups = []

    def handle(piece):
        had_lookups = bool(lookups)
        text, actions = extract_actions(piece)
        for name, args in actions if USE_TOOLS else []:
            if name == "show_emotion":
                show_emotion(str(_arg(args, "emotion", "name", "symbol")))
            elif (name, args) not in lookups:
                lookups.append((name, args))
        if text and not had_lookups:
            turn_has_spoken.set()
            Bridge.call("set_state", SPEAKING)
            voice.say(text)

    print("🧠 AI thinking (local): ", end="", flush=True)
    buffer = ""
    stream = llm.chat_stream(prompt)
    try:
        for chunk in stream:
            print(chunk, end="", flush=True)
            buffer += chunk
            # Hand every finished sentence to the speech queue right away,
            # so speech starts before the LLM has finished generating.
            pieces, buffer = split_speakable(buffer)
            for piece in pieces:
                handle(piece)
            if lookups:
                break  # the model asked for data: whatever it writes next would be made up
        else:
            if buffer.strip():
                handle(buffer)
    finally:
        stream.close()
    print()
    return lookups


def think_and_speak(command):
    """Answer the user: stream the LLM reply, look things up if it asks for it, speak it."""
    global emotion_shown
    emotion_shown = False
    turn_has_spoken.clear()
    local_time = now_local().strftime("%I:%M %p, %A, %B %d, %Y")
    prompt = (
        f"[System info: The current local time and date is {local_time} ({part_of_day()})]\n\n"
        f"User: {command}"
    )

    # If the model is slow to start, say "hmm..." like a person thinking
    def thinking_filler():
        if not turn_has_spoken.is_set():
            say_filler(THINKING_FILLERS)

    filler_timer = threading.Timer(THINKING_FILLER_SECONDS, thinking_filler)
    filler_timer.daemon = True
    filler_timer.start()
    try:
        for lookup_round in range(MAX_LOOKUP_ROUNDS + 1):
            lookups = answer_round(prompt)
            if not lookups:
                break
            if lookup_round == MAX_LOOKUP_ROUNDS:
                voice.say("Sorry, I couldn't find that out right now.")
                break
            prompt = run_lookups(lookups)
    finally:
        filler_timer.cancel()

    # Wait until everything is spoken, so the microphone doesn't hear the assistant itself
    voice.wait()
    time.sleep(EMOTION_HOLD_SECONDS if emotion_shown else 0.3)


def loop():
    global app_state, user_text

    if app_state == "IDLE":
        update_idle_face()
        time.sleep(0.1)
        return

    if app_state in ("LISTENING", "FOLLOW_UP"):
        just_woke = app_state == "LISTENING"
        if just_woke:
            play_earcon("wake", block=True)  # "I'm listening" chime
        user_text = listen(COMMAND_SECONDS if just_woke else FOLLOW_UP_SECONDS)

        if not user_text:
            if just_woke:
                # Woken up but heard nothing: say so, like a person would
                Bridge.call("set_state", SPEAKING)
                voice.say(random.choice(DIDNT_CATCH), wait=True)
            end_conversation("\n🤷 Nothing heard. Conversation ended.")
            return
        print(f"\n🗣️ You said: {user_text}")
        said = normalize(user_text)

        if wants_to_stop(user_text):
            Bridge.call("set_state", SPEAKING)
            voice.say(farewell(), wait=True)
            end_conversation("👋 Conversation ended by user.")
            return

        # Instant, natural replies to small talk (no need to wait for the LLM)
        if THANKS_RE.match(said) or GREETING_RE.match(said):
            reply = random.choice(THANKS_REPLIES) if THANKS_RE.match(said) else greeting_reply()
            print(f"💬 Quick reply: {reply}")
            display_emotion("happy")
            Bridge.call("set_state", SPEAKING)
            voice.say(reply, wait=True)
            time.sleep(1.0)
            app_state = "FOLLOW_UP"
            return

        Bridge.call("set_state", PROCESSING)
        app_state = "PROCESSING"
        return

    if app_state == "PROCESSING":
        try:
            think_and_speak(user_text)
            app_state = "FOLLOW_UP"  # keep the conversation going, no wake word needed
        except Exception as e:
            print(f"\n❌ Error during LLM/TTS: {e}")
            apologize()
            end_conversation("🔄 Returning to sleep mode...", sound=None)


def apologize():
    """Tell the user something went wrong instead of silently going back to sleep."""
    try:
        voice.stop_talking()
        play_earcon("error", block=True)
        Bridge.call("set_state", SPEAKING)
        voice.say(random.choice(ERROR_REPLIES), wait=True)
    except Exception as e:
        print(f"⚠️ Could not apologize: {e}")


try:
    App.run(user_loop=loop)
except KeyboardInterrupt:
    print("\nStopping application...")
finally:
    voice.stop_talking()
    mic.stop()
    Bridge.call("set_state", IDLE)
