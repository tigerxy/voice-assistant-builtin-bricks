"""The conversation flow.

    IDLE --"Hey Arduino"--> LISTENING --text--> THINKING --answer--> FOLLOW_UP --text--> THINKING ...
                                 |                                       |
                                 +------- silence / "bye" / error -------+--> IDLE
"""

import random
import time

from .face import IDLE_AWAKE, IDLE_OFF, IDLE_SLEEPING
from .timeofday import is_night, part_of_day

IDLE, LISTENING, FOLLOW_UP, THINKING = "IDLE", "LISTENING", "FOLLOW_UP", "THINKING"


class Conversation:
    def __init__(self, ears, brain, expression, language, settings, now):
        self.ears = ears
        self.brain = brain
        self.expression = expression
        self.face = expression.face
        self.sounds = expression.sounds
        self.language = language
        self.settings = settings
        self.now = now
        self.state = IDLE
        self.user_text = ""
        self.last_activity = time.monotonic()  # end of the last conversation (or start)
        self.update_idle_face()
        self.face.idle()

    def wake(self) -> None:
        """Called by the wake word detector (from its own thread)."""
        if self.state == IDLE:
            print("\n✨ Wake word 'Hey Arduino' detected!")
            self.state = LISTENING

    def step(self) -> None:
        """One iteration of the main loop (App.run calls this over and over)."""
        if self.state == IDLE:
            self.update_idle_face()
            time.sleep(0.1)
        elif self.state in (LISTENING, FOLLOW_UP):
            self._listen()
        elif self.state == THINKING:
            self._think()

    def update_idle_face(self) -> None:
        """Awake eyes after a conversation, sleeping face after a while without one."""
        if self.settings.idle_face == "off":
            mode = IDLE_OFF
        elif self.settings.idle_face == "awake":
            mode = IDLE_AWAKE
        else:
            idle_for = time.monotonic() - self.last_activity
            mode = IDLE_SLEEPING if idle_for >= self.settings.sleep_after_seconds else IDLE_AWAKE
        self.face.set_idle_mode(mode)

    def shutdown(self) -> None:
        self.expression.voice.stop_talking()
        self.ears.stop()
        self.face.idle()

    # --- states -------------------------------------------------------------------
    def _listen(self):
        just_woke = self.state == LISTENING
        if just_woke:
            self.sounds.play("wake", block=True)  # "I'm listening" chime
        self.face.listening()
        wait = self.settings.wait_for_speech_seconds if just_woke else self.settings.follow_up_wait_seconds
        self.user_text = self.ears.listen(wait)
        said = self.user_text
        lang = self.language

        if not said:
            if just_woke:  # woken up but heard nothing: say so, like a person would
                self.expression.say_one_of(lang.didnt_catch, wait=True)
            self._end("\n🤷 Nothing heard. Conversation ended.")
            return
        print(f"\n🗣️ You said: {said}")

        if lang.is_exit(said):
            self.expression.say_one_of(lang.farewells[self._farewell_time()], wait=True)
            self._end("👋 Conversation ended by user.")
            return

        # Instant, natural replies to small talk (no need to wait for the LLM)
        if lang.is_thanks(said) or lang.is_greeting(said):
            replies = lang.thanks_replies if lang.is_thanks(said) else lang.greetings[self._part_of_day()]
            reply = random.choice(replies)
            print(f"💬 Quick reply: {reply}")
            self.expression.show_emotion("happy")
            self.expression.say(reply, wait=True)
            time.sleep(1.0)
            self.state = FOLLOW_UP
            return

        self.face.thinking()
        self.state = THINKING

    def _think(self):
        try:
            self.brain.answer(self.user_text)
            self.state = FOLLOW_UP  # keep the conversation going, no wake word needed
        except Exception as e:
            print(f"\n❌ Error during LLM/TTS: {e}")
            self.expression.apologize()
            self._end("🔄 Returning to sleep mode...", sound=None)

    def _end(self, message, sound="end"):
        print(message)
        if sound:
            self.sounds.play(sound, block=True)
        self.brain.forget()  # the next "Hey Arduino" starts a fresh conversation
        self.last_activity = time.monotonic()  # wide awake again; the sleep countdown restarts
        self.update_idle_face()
        self.face.idle()
        self.state = IDLE
        print("💤 Listening for 'Hey Arduino'...")

    def _part_of_day(self):
        return part_of_day(self.now().hour)

    def _farewell_time(self):
        return "night" if is_night(self.now().hour, self.settings.night_hours) else self._part_of_day()
