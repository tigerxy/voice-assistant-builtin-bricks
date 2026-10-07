"""The LED matrix face, drawn by sketch.ino. Numbers and names must match the sketch."""

from arduino.app_utils import Bridge

# set_state
IDLE, LISTENING, THINKING, SPEAKING = 0, 1, 2, 3
# set_idle_mode
IDLE_AWAKE, IDLE_SLEEPING, IDLE_OFF = 0, 1, 2
# show_emotion: index into EMOTION_BITMAPS in sketch.ino (same order!)
EMOTIONS = ["heart", "happy", "sad", "surprised", "wink", "angry", "confused", "star"]


class Face:
    def __init__(self):
        self._idle_mode = None

    def idle(self) -> None:
        Bridge.call("set_state", IDLE)

    def listening(self) -> None:
        Bridge.call("set_state", LISTENING)

    def thinking(self) -> None:
        Bridge.call("set_state", THINKING)

    def speaking(self) -> None:
        Bridge.call("set_state", SPEAKING)

    def show_emotion(self, name: str) -> bool:
        """Show an emotion symbol until the assistant listens again. False if unknown."""
        if name not in EMOTIONS:
            return False
        Bridge.call("show_emotion", EMOTIONS.index(name))
        return True

    def set_idle_mode(self, mode: int) -> None:
        """How the face looks while idle; only sent to the sketch when it changes."""
        if mode != self._idle_mode:
            Bridge.call("set_idle_mode", mode)
            self._idle_mode = mode
