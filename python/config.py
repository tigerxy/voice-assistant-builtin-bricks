"""Settings of the voice assistant. This is the only file you normally need to edit.

Switching the language (English "en" / German "de"):
  1. set LANGUAGE below, and
  2. set the matching TTS voice in app.yaml (the app warns at start-up if they don't match):
       - arduino:tts:
           model: piper-tts-de      # or piper-tts-en
"""

from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

LANGUAGE = "en"  # "en" or "de"


@dataclass
class Settings:
    language: str = LANGUAGE
    time_zone: ZoneInfo = field(default_factory=lambda: ZoneInfo("Europe/Berlin"))

    # Listening
    wake_word: str = "hey_arduino"   # the built-in keyword spotting model only knows this one
    wake_confidence: float = 0.90
    command_seconds: int = 7         # how long to listen right after the wake word
    follow_up_seconds: int = 6       # how long to wait for a reply before ending the conversation

    # Thinking
    memory_messages: int = 16        # conversation history kept for the LLM
    use_tools: bool = True           # web search, weather and emotions
    max_lookup_rounds: int = 2       # how often the LLM may look something up before it has to answer
    search_timeout: float = 8        # seconds per web request
    thinking_filler_seconds: float = 3.0  # say "hmm..." if the first words take longer than this

    # Sounds and LED face
    sound_effects: bool = True
    sound_volume: float = 0.35       # 0.0 .. 1.0
    emotion_hold_seconds: float = 2.0     # keep an emotion symbol visible after speaking
    idle_face: str = "auto"          # "auto" (falls asleep after inactivity), "awake" or "off"
    sleep_after_seconds: float = 5 * 60   # sleeping face after this long without a conversation
    night_hours: tuple = (22, 7)     # for wording only, e.g. "Good night!" from 22:00 to 07:00


SETTINGS = Settings()
