import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from requests.exceptions import ConnectionError, ReadTimeout

from arduino.app_utils import App, Bridge
from arduino.app_bricks.llm import LargeLanguageModel
from arduino.app_bricks.asr import AutomaticSpeechRecognition
from arduino.app_bricks.keyword_spotting import KeywordSpotting
from arduino.app_bricks.tts import TextToSpeech
from arduino.app_peripherals.microphone import Microphone

# Change this to your own time zone (IANA name).
TIME_ZONE = ZoneInfo("Europe/Berlin")

# Bridge states understood by sketch.ino
IDLE, LISTENING, PROCESSING, SPEAKING = 0, 1, 2, 3

# A sentence ends at . ! ? followed by whitespace
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")

print("=" * 50)
print("🚀 PHASE 1: Loading LOCAL LLM into RAM...")
print("=" * 50)

llm = LargeLanguageModel(
    system_prompt=(
        "You are a helpful voice assistant. Keep your answers brief, conversational, "
        "and maximum two sentences long. Whenever your answer includes a symbol or unit "
        "change it by its word; for example 86% = 86 percent and avoid emojis."
    )
)
llm.with_memory(5)

try:
    print("⏳ Moving model to NPU. Please wait a moment...")
    llm.chat("Respond 'ok'")
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

app_state = "IDLE"
user_text = ""


def on_keyword_detected():
    global app_state
    if app_state == "IDLE":
        print("\n✨ Wake word 'VENTUNO' detected!")
        app_state = "LISTENING"


spotter = KeywordSpotting(mic=mic_spotter, confidence=0.90, debounce_sec=2.0)
spotter.on_detect("Ventuno", on_keyword_detected)

mic_spotter.start()
asr.start()
spotter.start()

Bridge.call("set_state", IDLE)
print("\n✅ All systems online! 💤 Listening for 'Ventuno'...")


def go_idle(message):
    global app_state
    print(message)
    Bridge.call("set_state", IDLE)
    app_state = "IDLE"


def listen():
    """Record and transcribe one voice command. Returns the text ('' if nothing heard)."""
    text = ""
    partial = ""

    mic_asr.start()
    time.sleep(0.1)
    Bridge.call("set_state", LISTENING)
    print("\n🟢 ASR ACTIVE: Tell me your command! (Speak now)...")

    try:
        with asr.transcribe_stream(duration=7) as stream:
            for chunk in stream:
                match chunk.type:
                    case "partial_text":
                        print(f"\r👂 Listening: {chunk.data}", end="", flush=True)
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


def think_and_speak(command):
    """Stream the LLM reply and speak it sentence by sentence as it arrives."""
    local_time = datetime.now(TIME_ZONE).strftime("%I:%M %p, %A, %B %d, %Y")
    prompt = (
        f"[System info: The current local time and date is {local_time}]\n\n"
        f"User command: {command}"
    )

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

    # Wait for the queued speech to finish playing
    while tts.is_speaking():
        time.sleep(0.1)


def loop():
    global app_state, user_text

    if app_state == "IDLE":
        time.sleep(0.1)
        return

    if app_state == "LISTENING":
        user_text = listen()
        if user_text:
            print(f"\n🗣️ You said: {user_text}")
            Bridge.call("set_state", PROCESSING)
            app_state = "PROCESSING"
        else:
            go_idle("\n🤷 No command heard. Returning to sleep...")
        return

    if app_state == "PROCESSING":
        try:
            think_and_speak(user_text)
        except (ReadTimeout, ConnectionError, RuntimeError) as e:
            print(f"\n⚠️ Container error during LLM/TTS: {e}")
            tts.cancel()
        except Exception as e:
            print(f"\n❌ Unexpected error during LLM/TTS: {e}")
            tts.cancel()
        go_idle("\n🔄 Returning to sleep mode...")


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
