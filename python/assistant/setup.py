"""Creates all parts of the assistant and connects them."""

import re
from datetime import datetime
from pathlib import Path

from arduino.app_bricks.llm import LargeLanguageModel
from arduino.app_bricks.tts import TextToSpeech

from languages import load_language

from .brain import Brain
from .conversation import Conversation
from .ears import Ears
from .expression import Expression
from .face import Face
from .sounds import Sounds
from .tools import Tools
from .voice import Voice

APP_YAML = Path(__file__).resolve().parent.parent.parent / "app.yaml"


def build_assistant(settings) -> Conversation:
    language = load_language(settings.language)
    print("=" * 50)
    print(f"🚀 Starting voice assistant ({language.name})")
    print("=" * 50)
    check_tts_model(language)

    def now():
        return datetime.now(settings.time_zone)

    # Bricks are started by App.run(); starting them here as well would
    # warm up the TTS and ASR models twice and slow down the start.
    # TextToSpeech() plays on the first plugged speaker. To force one, e.g. ALSA card 1:
    #   from arduino.app_peripherals.speaker import Speaker
    #   TextToSpeech(speaker=Speaker("plughw:1,0", sample_rate=Speaker.RATE_44K, shared=True))
    expression = Expression(
        voice=Voice(TextToSpeech()),
        face=Face(),
        sounds=Sounds(settings.sound_effects, settings.sound_volume),
        language=language,
        settings=settings,
    )
    tools = Tools(expression, language, settings)

    system_prompt = language.system_prompt + (language.tools_prompt if settings.use_tools else "")
    llm = LargeLanguageModel(
        system_prompt=system_prompt,
        tools=tools.for_llm() if settings.use_tools else None,
    )
    llm.with_memory(settings.memory_messages)
    warm_up(llm)

    brain = Brain(llm, tools, expression, language, settings, now)
    conversation = None  # created below; the wake word callback needs it

    def on_wake():
        conversation.wake()

    ears = Ears(settings, language, on_wake)
    conversation = Conversation(ears, brain, expression, language, settings, now)
    print("\n✅ Ready! 💤 Listening for 'Hey Arduino'...")
    return conversation


def warm_up(llm) -> None:
    """Load the model onto the NPU now, not on the first question."""
    try:
        print("⏳ Loading the LLM onto the NPU. Please wait a moment...")
        llm.chat("Respond 'ok'")
        llm.clear_memory()  # don't keep the warm-up in the conversation
        print("✅ LLM loaded.")
    except Exception as e:
        print(f"❌ Critical error loading LLM: {e}")


def configured_tts_model(app_yaml: Path = APP_YAML):
    """TTS model set in app.yaml, or None for the brick's default (English)."""
    try:
        text = app_yaml.read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r"-\s*arduino:tts:\s*\n\s+model:\s*([\w.-]+)", text)
    return match.group(1) if match else None


def check_tts_model(language, app_yaml: Path = APP_YAML) -> bool:
    """Warn if the TTS voice in app.yaml doesn't speak the configured language."""
    model = configured_tts_model(app_yaml) or "piper-tts-en"

    def simplify(name):
        return name.replace("-", "").replace("_", "").lower()

    if simplify(model) != simplify(language.tts_model):
        print(f"⚠️ LANGUAGE is '{language.code}' but app.yaml uses the TTS model '{model}'. "
              f"Set it to '{language.tts_model}' (see config.py).")
        return False
    return True
