"""Microphone, wake word and speech recognition."""

import time

from requests.exceptions import ConnectionError, ReadTimeout

from arduino.app_bricks.asr import AutomaticSpeechRecognition
from arduino.app_bricks.keyword_spotting import KeywordSpotting
from arduino.app_peripherals.microphone import Microphone


class Ears:
    def __init__(self, settings, language, on_wake):
        # Microphone for speech recognition. It is only switched on while the assistant
        # listens, so it never records the assistant's own voice.
        # The wake word detector opens its own stream of the same physical microphone
        # (ALSA shared mode). The two must not read from one Microphone object: each
        # read takes the chunk away from the other, so both would get half the audio.
        self.mic = Microphone()
        self.asr = AutomaticSpeechRecognition(self.mic, language=language.asr_language)

        # The brick only accepts plain functions as callbacks (not methods)
        def wake_word_heard():
            on_wake()

        self.wake_word = KeywordSpotting(confidence=settings.wake_confidence, debounce_sec=2.0)
        self.wake_word.on_detect(settings.wake_word, wake_word_heard)

    def listen(self, seconds: int) -> str:
        """Record and transcribe one utterance. Returns the text ('' if nothing heard)."""
        text = ""
        partial = ""
        self.mic.start()
        time.sleep(0.1)
        print(f"\n🟢 Listening ({seconds}s)...")
        try:
            with self.asr.transcribe_stream(duration=seconds) as stream:
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
        self.mic.stop()  # closed while the assistant talks, so it never hears itself
        return text or partial

    def stop(self) -> None:
        self.mic.stop()
