"""Microphone, wake word and speech recognition."""

import threading
import time

from requests.exceptions import ConnectionError, ReadTimeout

from arduino.app_bricks.asr import AutomaticSpeechRecognition
from arduino.app_bricks.keyword_spotting import KeywordSpotting
from arduino.app_peripherals.microphone import Microphone


class Ears:
    def __init__(self, settings, language, on_wake):
        self.settings = settings

        # Microphone for speech recognition. It is only switched on while the assistant
        # listens, so it never records the assistant's own voice.
        # The wake word detector opens its own stream of the same physical microphone
        # (ALSA shared mode). The two must not read from one Microphone object: each
        # read takes the chunk away from the other, so both would get half the audio.
        self.mic = Microphone()
        self.asr = AutomaticSpeechRecognition(self.mic, language=language.asr_language)
        # How long a pause must be to end a sentence. The brick has no public setting
        # for it, only this class default that it sends to the speech service.
        if hasattr(self.asr, "_DEFAULT_VAD_MS"):
            self.asr._DEFAULT_VAD_MS = settings.end_of_speech_ms

        # The brick only accepts plain functions as callbacks (not methods)
        def wake_word_heard():
            on_wake()

        self.wake_word = KeywordSpotting(confidence=settings.wake_confidence, debounce_sec=2.0)
        self.wake_word.on_detect(settings.wake_word, wake_word_heard)

    def listen(self, wait_for_speech: float) -> str:
        """Listen to one sentence and return it ('' if nobody spoke).

        No fixed recording length: the speech service's voice activity detector
        ends the sentence when you stop talking (after end_of_speech_ms of silence).
        If nobody starts talking within wait_for_speech seconds, listening stops early.
        """
        text = ""
        partial = ""
        heard = threading.Event()

        def nobody_spoke():
            if not heard.is_set():
                print("\n🔇 Nobody spoke.")
                self.asr.cancel()

        watchdog = threading.Timer(wait_for_speech, nobody_spoke)
        watchdog.daemon = True

        self.mic.start()
        time.sleep(0.1)
        print("\n🟢 Listening...")
        try:
            watchdog.start()
            with self.asr.transcribe_sentence_stream(timeout=self.settings.max_sentence_seconds) as stream:
                for chunk in stream:
                    if chunk.data.strip():
                        heard.set()  # someone is talking: wait for the end of the sentence
                    match chunk.type:
                        case "partial_text":
                            print(f"\r👂 {chunk.data}", end="", flush=True)
                            partial = chunk.data.strip()
                        case "full_text":
                            text = chunk.data.strip()
                            if text:
                                break  # the VAD detected the end of the sentence
        except (ReadTimeout, ConnectionError) as e:
            print(f"\n⚠️ Container connection timeout: {e}")
        except RuntimeError as e:
            print(f"\n⚠️ ASR container error: {e}")
        except Exception as e:
            print(f"\n⚠️ Unexpected ASR error: {e}")
        finally:
            watchdog.cancel()

        time.sleep(1.5)  # let ASR release its resources
        self.mic.stop()  # closed while the assistant talks, so it never hears itself
        return text or partial

    def stop(self) -> None:
        self.mic.stop()
