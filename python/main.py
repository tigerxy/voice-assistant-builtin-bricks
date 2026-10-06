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
MEMORY_MESSAGES = 16       # conversation history kept for the LLM (tool calls count too)
USE_TOOLS = True           # set False if your LLM runner doesn't support tool calling
SEARCH_TIMEOUT = 8         # seconds per web request
SEARCH_LANG = "en"         # Wikipedia language edition used by web_search
SOUND_EFFECTS = True       # short chimes and jingles via the sound_generator brick
SOUND_VOLUME = 0.35        # 0.0 .. 1.0
THINKING_FILLER_SECONDS = 3.0  # say "hmm..." if the first words take longer than this
IDLE_FACE = "auto"         # "auto" (awake by day, sleeping at night), "awake" or "off"
SLEEP_HOURS = (22, 7)      # night time for the sleeping face: from 22:00 to 07:00

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

SENTENCE_END = re.compile(r"(?<=[.!?])\s+")

SYSTEM_PROMPT = (
    "You are a friendly voice assistant having a spoken conversation. "
    "Sound natural and warm, like a person: vary how you start your sentences, and feel free to use "
    "small interjections such as oh, hmm or well. Don't greet the user unless they greet you. "
    "Keep each answer brief and conversational, at most two or three sentences. "
    "Write numbers, symbols and units as words, for example 86% becomes 86 percent, and never use emojis or lists. "
    "The conversation continues after your answer, so you may ask a short follow-up question when it helps. "
    "Use the web_search tool when you need facts you are not sure about, details about people, places or things, "
    "or anything that may have changed recently. Use the get_weather tool for weather questions. "
    "Do not use web_search or get_weather for small talk or things you already know well. "
    "When your answer has a clear feeling, call show_emotion once to show a matching symbol on your LED face, "
    "for example heart for affection or love, happy for joy, sad for bad news, surprised for amazing facts, "
    "wink for jokes, confused when you don't understand, star for praise or success. Never mention the symbol in your answer."
)

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
    start, end = SLEEP_HOURS
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
    tts.speak(random.choice(options), block=False)


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
    days_ahead = max(0, min(int(days_ahead), 6))
    print(f"\n🌦️ Getting weather for {city} (+{days_ahead} days)")
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


# ---------------------------------------------------------------------------
# Bricks
# ---------------------------------------------------------------------------
print("=" * 50)
print("🚀 PHASE 1: Loading LOCAL LLM into RAM...")
print("=" * 50)

llm = LargeLanguageModel(
    system_prompt=SYSTEM_PROMPT,
    tools=[web_search, get_weather, show_emotion] if USE_TOOLS else None,
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
tts = TextToSpeech()
print("⏳ Warming up TTS...")
tts.start()  # opens the speaker and pre-loads the TTS model
print("✅ TTS ready.")

if SOUND_EFFECTS:
    try:
        # Shares the speaker with TTS (both open it in shared mode)
        sfx = SoundGenerator(wave_form="sine", sound_effects=[SoundEffect.adsr()])
        sfx.start()
        print("✅ Sound effects ready.")
    except Exception as e:
        print(f"⚠️ Sound effects disabled: {e}")
        sfx = None

mic_spotter = Microphone()
mic_asr = Microphone()
asr = AutomaticSpeechRecognition(mic_asr)

# IDLE -> LISTENING -> PROCESSING -> FOLLOW_UP -> PROCESSING -> ... -> IDLE
app_state = "IDLE"
user_text = ""


def on_keyword_detected():
    global app_state
    if app_state == "IDLE":
        print("\n✨ Wake word 'Hey Arduino' detected!")
        app_state = "LISTENING"


spotter = KeywordSpotting(mic=mic_spotter, confidence=0.90, debounce_sec=2.0)
# The built-in keyword spotting model only knows "hey_arduino"
spotter.on_detect("hey_arduino", on_keyword_detected)

mic_spotter.start()
asr.start()
spotter.start()

current_idle_mode = None
next_idle_check = 0.0


def update_idle_face(force=False):
    """Awake eyes by day, sleeping face at night (checked every 30 s while idle)."""
    global current_idle_mode, next_idle_check
    if not force and time.monotonic() < next_idle_check:
        return
    next_idle_check = time.monotonic() + 30
    if IDLE_FACE == "off":
        mode = IDLE_OFF
    elif IDLE_FACE == "awake":
        mode = IDLE_AWAKE
    else:
        mode = IDLE_SLEEPING if is_night() else IDLE_AWAKE
    if mode != current_idle_mode:
        Bridge.call("set_idle_mode", mode)
        current_idle_mode = mode


update_idle_face(force=True)
Bridge.call("set_state", IDLE)
print("\n✅ All systems online! 💤 Listening for 'Hey Arduino'...")


# ---------------------------------------------------------------------------
# Conversation
# ---------------------------------------------------------------------------
def end_conversation(message, sound="end"):
    global app_state
    print(message)
    if sound:
        play_earcon(sound, block=True)
    llm.clear_memory()  # the next "Hey Arduino" starts a fresh conversation
    Bridge.call("set_state", IDLE)
    app_state = "IDLE"
    print("💤 Listening for 'Hey Arduino'...")


def listen(seconds):
    """Record and transcribe one utterance. Returns the text ('' if nothing heard)."""
    text = ""
    partial = ""

    mic_asr.start()
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
    mic_asr.stop()
    return text or partial


def wants_to_stop(text):
    t = text.lower().strip(" .!?,")
    return any(t == p or t.startswith(p + " ") or t.endswith(" " + p) for p in EXIT_PHRASES)


def think_and_speak(command):
    """Stream the LLM reply (with tool calls if needed) and speak it sentence by sentence."""
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

    def speak_sentence(sentence):
        turn_has_spoken.set()
        Bridge.call("set_state", SPEAKING)
        tts.speak(sentence, block=False)

    print("🧠 AI thinking (local): ", end="", flush=True)
    buffer = ""
    try:
        for chunk in llm.chat_stream(prompt):
            print(chunk, end="", flush=True)
            buffer += chunk

            # Hand every finished sentence to the TTS queue right away,
            # so speech starts before the LLM has finished generating.
            parts = SENTENCE_END.split(buffer)
            for sentence in parts[:-1]:
                if sentence.strip():
                    speak_sentence(sentence)
            buffer = parts[-1]
    finally:
        filler_timer.cancel()
    print()

    if buffer.strip():
        speak_sentence(buffer)

    # Wait until everything is spoken, so the microphone doesn't hear the assistant itself
    while tts.is_speaking():
        time.sleep(0.1)
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
                tts.speak(random.choice(DIDNT_CATCH))
            end_conversation("\n🤷 Nothing heard. Conversation ended.")
            return
        print(f"\n🗣️ You said: {user_text}")
        said = normalize(user_text)

        if wants_to_stop(user_text):
            Bridge.call("set_state", SPEAKING)
            tts.speak(farewell())
            end_conversation("👋 Conversation ended by user.")
            return

        # Instant, natural replies to small talk (no need to wait for the LLM)
        if THANKS_RE.match(said) or GREETING_RE.match(said):
            reply = random.choice(THANKS_REPLIES) if THANKS_RE.match(said) else greeting_reply()
            print(f"💬 Quick reply: {reply}")
            display_emotion("happy")
            Bridge.call("set_state", SPEAKING)
            tts.speak(reply)
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
        tts.cancel()
        play_earcon("error", block=True)
        Bridge.call("set_state", SPEAKING)
        tts.speak(random.choice(ERROR_REPLIES))
    except Exception as e:
        print(f"⚠️ Could not apologize: {e}")


try:
    App.run(user_loop=loop)
except KeyboardInterrupt:
    print("\nStopping application...")
finally:
    tts.stop()
    if sfx:
        sfx.stop()
    mic_spotter.stop()
    mic_asr.stop()
    asr.stop()
    Bridge.call("set_state", IDLE)
