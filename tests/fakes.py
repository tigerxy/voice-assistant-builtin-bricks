"""Fake versions of the Arduino App Lab bricks (and `requests`) used by python/main.py.

They let the app run on any computer without the VENTUNO Q hardware.

The method signatures copy the brick library that runs on the board
(arduino_app_bricks 0.11/0.12), not the newest version on GitHub. For example
TextToSpeech.speak(text) has no `block` argument there, so calling it with one
fails here just like on the board.

Every fake
writes what happens into one shared, ordered event log, so tests can check both
*what* the assistant did and *in which order* (e.g. "chime before listening").

Events are tuples:
    ("bridge", rpc_name, arg)      Bridge.call to the MCU sketch
    ("speak", text)                TextToSpeech.speak (blocks while "speaking")
    ("tts_busy", text)             speak() called while another speak() was still running
    ("tone", note)                 SoundGenerator.play_tone
    ("listen", seconds, mic_on)    ASR started; was its microphone on?
    ("mic", "start" | "stop", owner)  a Microphone started/stopped; owner is "asr" or "kws"
    ("app", "start_brick" | "stop_brick", brick_class_name)
    ("llm", prompt)                LLM chat_stream called
    ("clear_memory",)              LLM memory cleared
    ("http", url)                  web request
    ("weather", city, kwargs)      WeatherForecast lookup
"""

import inspect
import sys
import threading
import types
from contextlib import contextmanager
from urllib.parse import quote


class World:
    """Shared state of all fakes. Reset before each test."""

    def __init__(self):
        self.events = []
        self.utterances = []        # what the "user" says on each listen; "" = silence
        self.llm_responder = None   # fn(prompt, tools) -> iterable of text chunks
        self.http_routes = {}       # url prefix -> json payload, or Exception to raise
        self.weather = ("Slight rain", "rainy")
        self.keyword_callbacks = {}

    def log(self, *event):
        self.events.append(event)

    def of(self, kind):
        return [e for e in self.events if e[0] == kind]

    def spoken(self):
        return [e[1] for e in self.of("speak")]

    def bridge(self, name=None):
        return [e for e in self.of("bridge") if name is None or e[1] == name]

    def index(self, predicate, start=0):
        for i in range(start, len(self.events)):
            if predicate(self.events[i]):
                return i
        return -1


world = World()


# --- arduino.app_utils --------------------------------------------------------
class _Bridge:
    @staticmethod
    def call(name, *args):
        world.log("bridge", name, args[0] if args else None)


class _App:
    @staticmethod
    def run(user_loop=None):
        pass  # tests drive main.loop() themselves

    @staticmethod
    def start_brick(brick):
        world.log("app", "start_brick", type(brick).__name__)
        brick.start()

    @staticmethod
    def stop_brick(brick):
        world.log("app", "stop_brick", type(brick).__name__)
        brick.stop()


# --- arduino.app_bricks.llm -----------------------------------------------------
class LargeLanguageModel:
    last = None

    def __init__(self, system_prompt: str = "", temperature=0.7, max_tokens: int = 512,
                 timeout=None, tools=None, model=None):
        # Same parameters as the real brick (no **kwargs: unknown arguments fail here too)
        self.system_prompt = system_prompt
        self.tools = {}
        for tool in tools or []:
            # Like the real brick, plain functions are wrapped with
            # StructuredTool.from_function, which needs a docstring and type hints.
            if not callable(tool):
                raise TypeError(f"{tool!r} is not a tool")
            hints = inspect.signature(tool).parameters
            if not tool.__doc__ or any(p.annotation is inspect.Parameter.empty for p in hints.values()):
                raise ValueError(f"tool {tool.__name__} needs a docstring and type hints")
            self.tools[tool.__name__] = tool
        self.memory = None
        LargeLanguageModel.last = self

    def with_memory(self, max_messages=10, persistence=None):
        self.memory = max_messages

    def chat(self, message, images=None):
        return "ok"

    def chat_stream(self, message, images=None):
        world.log("llm", message)
        if world.llm_responder is None:
            yield "Sure thing."
            return
        yield from world.llm_responder(message, self.tools)

    def clear_memory(self):
        world.log("clear_memory")


# --- arduino.app_bricks.asr ---------------------------------------------------
class _Chunk:
    def __init__(self, type_, data):
        self.type = type_
        self.data = data


class AutomaticSpeechRecognition:
    def __init__(self, mic=None, language=None):
        self.language = language
        self.mic = mic if mic is not None else Microphone()
        self.mic.owner = "asr"

    def start(self):
        pass

    def stop(self):
        pass

    @contextmanager
    def transcribe_stream(self, duration=7):
        world.log("listen", duration, self.mic.started)
        text = world.utterances.pop(0) if world.utterances else ""
        chunks = []
        if text:
            chunks = [_Chunk("partial_text", text[: len(text) // 2]), _Chunk("full_text", text)]
        yield iter(chunks)


# --- arduino.app_bricks.keyword_spotting ----------------------------------------
class KeywordSpotting:
    instances = []

    def __init__(self, mic=None, confidence=0.8, debounce_sec=2.0):
        self.confidence = confidence
        # Like the real brick: without a mic it opens its own stream (model's sample rate)
        self.mic = mic if mic is not None else Microphone(0, sample_rate=16000, channels=1)
        self.mic.owner = "kws"
        self.running = False
        KeywordSpotting.instances.append(self)

    def on_detect(self, label, callback):
        world.keyword_callbacks[label] = callback

    def start(self):
        self.running = True
        self.mic.start()

    def stop(self):
        self.running = False
        self.mic.stop()


# --- arduino.app_bricks.tts ---------------------------------------------------
class TTSBusyError(Exception):
    pass


class TextToSpeech:
    """Same API as the TTS brick in arduino_app_bricks 0.11/0.12 (on the board):
    speak(text) blocks until spoken and refuses to run twice at the same time.
    There is no speak(block=...) and no is_speaking()."""

    SPEAK_SECONDS = 0.01

    def __init__(self, speaker=None):
        self._session = threading.Lock()

    def start(self):
        pass

    def stop(self):
        pass

    def speak(self, text: str):
        if not self._session.acquire(blocking=False):
            world.log("tts_busy", text)
            raise TTSBusyError("A speech session is already active on this instance.")
        try:
            world.log("speak", text)
            wait(self.SPEAK_SECONDS)
        finally:
            self._session.release()

    def cancel(self):
        world.log("tts_cancel")


# --- arduino.app_bricks.weather_forecast --------------------------------------------
class _WeatherData:
    def __init__(self, description, category):
        self.description = description
        self.category = category


class WeatherForecast:
    def get_forecast_by_city(self, city, timezone="GMT", forecast_days=1):
        world.log("weather", city, {"timezone": timezone, "forecast_days": forecast_days})
        return _WeatherData(*world.weather)


# --- arduino.app_bricks.sound_generator ---------------------------------------------
class SoundEffect:
    @staticmethod
    def adsr(*args, **kwargs):
        return "adsr"


class SoundGenerator:
    def __init__(self, output_device=None, wave_form="sine", sound_effects=None, **kwargs):
        pass

    def start(self):
        pass

    def stop(self):
        pass

    def play_tone(self, note, duration=0.25, volume=None, block=False):
        world.log("tone", note)


# --- arduino.app_peripherals.microphone ----------------------------------------------
class Microphone:
    instances = []

    def __init__(self, *args, **kwargs):
        self.started = False
        self.owner = None
        Microphone.instances.append(self)

    def start(self):
        if not self.started:  # like the real one: starting twice is a no-op
            self.started = True
            world.log("mic", "start", self.owner)

    def stop(self):
        if self.started:
            self.started = False
            world.log("mic", "stop", self.owner)


# --- requests (only what main.py uses) --------------------------------------------
class _Response:
    def __init__(self, payload):
        self._payload = payload
        self.ok = True
        self.status_code = 200

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


class _Session:
    def __init__(self):
        self.headers = {}

    def get(self, url, params=None, timeout=None):
        world.log("http", url)
        for prefix, payload in world.http_routes.items():
            if url.startswith(prefix):
                if isinstance(payload, Exception):
                    raise payload
                return _Response(payload)
        raise _ConnectionError(f"no route for {url}")


class _ConnectionError(Exception):
    pass


class _ReadTimeout(Exception):
    pass


def install():
    """Register the fake modules in sys.modules (idempotent)."""

    def module(name, **attrs):
        m = types.ModuleType(name)
        m.__dict__.update(attrs)
        sys.modules[name] = m
        return m

    module("arduino")
    module("arduino.app_utils", App=_App, Bridge=_Bridge)
    module("arduino.app_bricks")
    module("arduino.app_bricks.llm", LargeLanguageModel=LargeLanguageModel)
    module("arduino.app_bricks.asr", AutomaticSpeechRecognition=AutomaticSpeechRecognition)
    module("arduino.app_bricks.keyword_spotting", KeywordSpotting=KeywordSpotting)
    module("arduino.app_bricks.tts", TextToSpeech=TextToSpeech, TTSBusyError=TTSBusyError)
    module("arduino.app_bricks.weather_forecast", WeatherForecast=WeatherForecast)
    module("arduino.app_bricks.sound_generator", SoundGenerator=SoundGenerator, SoundEffect=SoundEffect)
    module("arduino.app_peripherals")
    module("arduino.app_peripherals.microphone", Microphone=Microphone)

    exceptions = module("requests.exceptions", ConnectionError=_ConnectionError, ReadTimeout=_ReadTimeout)
    utils = module("requests.utils", quote=quote)
    module("requests", Session=_Session, exceptions=exceptions, utils=utils)


def reset():
    global world
    world.__init__()
    Microphone.instances.clear()
    KeywordSpotting.instances.clear()
    return world


# A tiny helper for tests that need the LLM to be slow without using time.sleep
def wait(seconds):
    threading.Event().wait(seconds)
