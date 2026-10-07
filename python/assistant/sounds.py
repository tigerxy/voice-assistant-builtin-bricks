"""Short sound effects, played with the sound_generator brick (shares the speaker with TTS)."""

from arduino.app_bricks.sound_generator import SoundEffect, SoundGenerator

# name -> [(note, seconds)], "REST" is a pause
EARCONS = {
    "wake":      [("E5", 0.07), ("A5", 0.12)],
    "end":       [("A5", 0.07), ("E5", 0.12)],
    "error":     [("C4", 0.15), ("REST", 0.05), ("C4", 0.2)],
    # one jingle per emotion, played together with the symbol on the LED matrix
    "heart":     [("C5", 0.09), ("E5", 0.09), ("G5", 0.09), ("C6", 0.25)],
    "happy":     [("G5", 0.08), ("C6", 0.15)],
    "sad":       [("G4", 0.2), ("E4", 0.2), ("C4", 0.35)],
    "surprised": [("C5", 0.05), ("G5", 0.05), ("C6", 0.15)],
    "wink":      [("E6", 0.06), ("C6", 0.1)],
    "angry":     [("C3", 0.12), ("REST", 0.04), ("B2", 0.25)],
    "confused":  [("E5", 0.12), ("D5", 0.12), ("F5", 0.2)],
    "star":      [("C6", 0.06), ("E6", 0.06), ("G6", 0.06), ("C7", 0.2)],
}


class Sounds:
    def __init__(self, enabled: bool = True, volume: float = 0.35):
        self.enabled = enabled
        self.volume = volume
        self.generator = None
        if enabled:
            try:
                self.generator = SoundGenerator(wave_form="sine", sound_effects=[SoundEffect.adsr()])
            except Exception as e:
                print(f"⚠️ Sound effects disabled: {e}")

    def play(self, name: str, block: bool = False) -> None:
        """Play a sound from EARCONS. A sound problem never breaks the conversation."""
        if not (self.enabled and self.generator and name in EARCONS):
            return
        try:
            for note, seconds in EARCONS[name]:
                self.generator.play_tone(note, seconds, volume=self.volume, block=block)
        except Exception as e:
            print(f"⚠️ Sound effect '{name}' failed: {e}")
