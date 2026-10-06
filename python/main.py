import re
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

# Saying one of these ends the conversation
EXIT_PHRASES = ("stop", "goodbye", "bye", "that's all", "that is all", "thank you, that's it", "never mind")

# Bridge states understood by sketch.ino
IDLE, LISTENING, PROCESSING, SPEAKING = 0, 1, 2, 3

# Emotion symbols on the LED matrix. Order must match EMOTION_BITMAPS in sketch.ino
EMOTIONS = ["heart", "happy", "sad", "surprised", "wink", "angry", "confused", "star"]
EMOTION_HOLD_SECONDS = 2.0  # keep the symbol visible a moment after speaking

SENTENCE_END = re.compile(r"(?<=[.!?])\s+")

SYSTEM_PROMPT = (
    "You are a friendly voice assistant having a spoken conversation. "
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
    try:
        data = _weather.get_forecast_by_city(city, timezone=str(TIME_ZONE), forecast_days=days_ahead + 1)
        return f"Weather in {city}: {data.description} ({data.category})."
    except Exception as e:
        return f"Weather lookup failed: {e}"


emotion_shown = False


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
    Bridge.call("show_emotion", EMOTIONS.index(name))
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

Bridge.call("set_state", IDLE)
print("\n✅ All systems online! 💤 Listening for 'Hey Arduino'...")


# ---------------------------------------------------------------------------
# Conversation
# ---------------------------------------------------------------------------
def end_conversation(message):
    global app_state
    print(message)
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
    local_time = datetime.now(TIME_ZONE).strftime("%I:%M %p, %A, %B %d, %Y")
    prompt = f"[System info: The current local time and date is {local_time}]\n\nUser: {command}"

    print("🧠 AI thinking (local): ", end="", flush=True)
    buffer = ""
    started_speaking = False

    for chunk in llm.chat_stream(prompt):
        print(chunk, end="", flush=True)
        buffer += chunk

        # Hand every finished sentence to the TTS queue right away,
        # so speech starts before the LLM has finished generating.
        parts = SENTENCE_END.split(buffer)
        for sentence in parts[:-1]:
            if sentence.strip():
                if not started_speaking:
                    Bridge.call("set_state", SPEAKING)
                    started_speaking = True
                tts.speak(sentence, block=False)
        buffer = parts[-1]
    print()

    if buffer.strip():
        if not started_speaking:
            Bridge.call("set_state", SPEAKING)
        tts.speak(buffer, block=False)

    # Wait until everything is spoken, so the microphone doesn't hear the assistant itself
    while tts.is_speaking():
        time.sleep(0.1)
    time.sleep(EMOTION_HOLD_SECONDS if emotion_shown else 0.3)


def loop():
    global app_state, user_text

    if app_state == "IDLE":
        time.sleep(0.1)
        return

    if app_state in ("LISTENING", "FOLLOW_UP"):
        seconds = COMMAND_SECONDS if app_state == "LISTENING" else FOLLOW_UP_SECONDS
        user_text = listen(seconds)

        if not user_text:
            end_conversation("\n🤷 Nothing heard. Conversation ended.")
            return
        print(f"\n🗣️ You said: {user_text}")

        if wants_to_stop(user_text):
            tts.speak("Okay, talk to you later.")
            end_conversation("👋 Conversation ended by user.")
            return

        Bridge.call("set_state", PROCESSING)
        app_state = "PROCESSING"
        return

    if app_state == "PROCESSING":
        try:
            think_and_speak(user_text)
            app_state = "FOLLOW_UP"  # keep the conversation going, no wake word needed
        except (ReadTimeout, ConnectionError, RuntimeError) as e:
            print(f"\n⚠️ Container error during LLM/TTS: {e}")
            tts.cancel()
            end_conversation("🔄 Returning to sleep mode...")
        except Exception as e:
            print(f"\n❌ Unexpected error during LLM/TTS: {e}")
            tts.cancel()
            end_conversation("🔄 Returning to sleep mode...")


try:
    App.run(user_loop=loop)
except KeyboardInterrupt:
    print("\nStopping application...")
finally:
    tts.stop()
    mic_spotter.stop()
    mic_asr.stop()
    asr.stop()
    Bridge.call("set_state", IDLE)
