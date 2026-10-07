"""Speech queue on top of the TTS brick."""

import queue
import threading


class Voice:
    """Speaks queued sentences one after another in a background thread.

    Lets the assistant start talking while the LLM is still generating. Only uses
    TextToSpeech.speak(text) (blocks until spoken), which every version of the TTS
    brick has, so it also works with the older library on the board.
    """

    def __init__(self, tts):
        self._tts = tts
        self._queue = queue.Queue()
        self._pending = 0
        self._generation = 0  # bumped by stop_talking() to drop queued sentences
        self._done = threading.Condition()
        threading.Thread(target=self._worker, name="Voice", daemon=True).start()

    def say(self, text: str, wait: bool = False) -> None:
        """Queue text to be spoken. With wait=True, return only when everything is spoken."""
        text = text.strip()
        if text:
            with self._done:
                self._pending += 1
                generation = self._generation
            self._queue.put((generation, text))
        if wait:
            self.wait()

    def is_busy(self) -> bool:
        with self._done:
            return self._pending > 0

    def wait(self) -> None:
        """Block until everything queued has been spoken."""
        with self._done:
            self._done.wait_for(lambda: self._pending == 0)

    def stop_talking(self) -> None:
        """Drop everything still queued and interrupt the current sentence."""
        with self._done:
            self._generation += 1
        try:
            self._tts.cancel()
        except Exception as e:
            print(f"⚠️ Could not cancel speech: {e}")

    def _worker(self) -> None:
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
