"""How the assistant expresses itself: speech, LED face and sounds, as one personality."""

import random
import threading
import time


class Expression:
    def __init__(self, voice, face, sounds, language, settings):
        self.voice = voice
        self.face = face
        self.sounds = sounds
        self.language = language
        self.settings = settings
        self.spoke = threading.Event()  # set as soon as anything is said in the current turn
        self.emotion_shown = False

    def new_turn(self) -> None:
        self.spoke.clear()
        self.emotion_shown = False

    def say(self, text: str, wait: bool = False) -> None:
        self.spoke.set()
        self.face.speaking()
        self.voice.say(text, wait=wait)

    def say_one_of(self, options: list, wait: bool = False) -> None:
        self.say(random.choice(options), wait=wait)

    def filler(self, options: list) -> None:
        """Say a filler like "hmm" or "let me check", unless something was said already."""
        if not self.spoke.is_set():
            self.say_one_of(options)

    def show_emotion(self, name: str) -> bool:
        """Emotion symbol on the LED face plus its jingle. False if the name is unknown."""
        if not self.face.show_emotion(name):
            return False
        self.sounds.play(name)
        self.emotion_shown = True
        return True

    def finish_turn(self) -> None:
        """Wait until everything is spoken (so the microphone doesn't hear it), keep an emotion visible."""
        self.voice.wait()
        time.sleep(self.settings.emotion_hold_seconds if self.emotion_shown else 0.3)

    def apologize(self) -> None:
        """Say something went wrong instead of silently going back to sleep."""
        try:
            self.voice.stop_talking()
            self.sounds.play("error", block=True)
            self.say_one_of(self.language.error_replies, wait=True)
        except Exception as e:
            print(f"⚠️ Could not apologize: {e}")
