"""Tests for sketch/sketch.ino (LED matrix face).

The real sketch is compiled for this computer with tiny stand-ins for the Arduino
libraries (tests/sketch_host/stubs) and run by tests/sketch_host/harness.cpp,
which prints the 13x8 frame for each scenario. Needs a C++17 compiler (g++ or clang++).
"""

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOST = ROOT / "tests" / "sketch_host"
SKETCH = ROOT / "sketch" / "sketch.ino"
COMPILER = shutil.which("g++") or shutil.which("clang++")

EYE_COLS = [(2, 3, 4), (8, 9, 10)]


def lit(frame):
    """Set of (row, col) that are on."""
    return {(r, c) for r in range(8) for c in range(13) if frame[r][c] > 0}


def sketch_bitmaps():
    """Emotion bitmaps as written in sketch.ino: {name: set of (row, col)}."""
    src = SKETCH.read_text()
    result = {}
    for name, body in re.findall(r"\{ // \d+: (\w+)[^\n]*\n(.*?)\}", src, re.S):
        rows = re.findall(r'"([.X]{13})"', body)
        result[name] = {(r, c) for r, row in enumerate(rows) for c, ch in enumerate(row) if ch == "X"}
    return result


@unittest.skipUnless(COMPILER, "no C++ compiler found")
class TestSketchFrames(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        exe = Path(cls.tmp.name) / "harness"
        build = subprocess.run(
            [COMPILER, "-std=c++17", "-Wall", "-Werror", "-Wno-unused-parameter",
             "-I", str(HOST / "stubs"), "-x", "c++", str(HOST / "harness.cpp"), "-o", str(exe)],
            capture_output=True, text=True,
        )
        if build.returncode != 0:
            raise AssertionError("sketch.ino does not compile:\n" + build.stderr)
        out = subprocess.run([str(exe)], capture_output=True, text=True, check=True).stdout

        cls.provides = set(re.search(r"^PROVIDES (.*)$", out, re.M).group(1).split(","))
        cls.grayscale = int(re.search(r"^GRAYSCALE (\d+)$", out, re.M).group(1))
        cls.frames = {}
        for name, rows in re.findall(r"^FRAME (\w+)\n((?:[0-7]{13}\n){8})", out, re.M):
            cls.frames[name] = [[int(ch) for ch in row] for row in rows.split()]
        cls.bitmaps = sketch_bitmaps()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def frame(self, name):
        self.assertIn(name, self.frames, f"harness did not produce frame {name}")
        return self.frames[name]

    def open_eyes(self, pupil_offset):
        cells = {(r, c) for cols in EYE_COLS for c in cols for r in range(2, 6)}
        pupils = {(r, cols[1] + pupil_offset) for cols in EYE_COLS for r in (3, 4)}
        return cells - pupils

    # --- RPC interface ---------------------------------------------------------------
    def test_provides_all_rpcs(self):
        self.assertEqual(self.provides, {"set_state", "show_emotion", "set_idle_mode"})

    def test_uses_3bit_grayscale(self):
        self.assertEqual(self.grayscale, 3)

    # --- Idle life ----------------------------------------------------------------
    def test_idle_awake_shows_two_dim_eyes_with_pupils(self):
        f = self.frame("idle_awake_center")
        self.assertEqual(lit(f), self.open_eyes(0))
        self.assertTrue(all(f[r][c] <= 3 for r, c in lit(f)), "idle eyes should be dim")

    def test_eyes_look_left_and_right(self):
        self.assertEqual(lit(self.frame("idle_awake_left")), self.open_eyes(-1))
        self.assertEqual(lit(self.frame("idle_awake_right")), self.open_eyes(1))

    def test_blink_closes_and_reopens_eyes(self):
        self.assertEqual(lit(self.frame("idle_blink")), {(4, c) for cols in EYE_COLS for c in cols})
        self.assertGreater(len(lit(self.frame("idle_after_blink"))), 6)

    def test_sleeping_face_closed_eyes_and_floating_z(self):
        closed = {(5, c) for cols in EYE_COLS for c in cols}
        t0, t1, gap = (lit(self.frame(n)) for n in ("sleep_t0", "sleep_t1", "sleep_gap"))
        self.assertEqual(gap, closed, "between two z's only the closed eyes are visible")
        z0, z1 = t0 - closed, t1 - closed
        self.assertTrue(closed <= t0 and closed <= t1)
        self.assertTrue(z0 and all(c >= 9 for _, c in z0), "z should float on the right")
        self.assertEqual({(r - 1, c) for r, c in z0}, z1, "z should move up one row per second")
        self.assertTrue(all(self.frame("sleep_t0")[r][c] == 1 for r, c in closed), "sleeping eyes should be very dim")

    def test_idle_off_clears_matrix(self):
        self.assertEqual(lit(self.frame("idle_off")), set())

    # --- Original animations still work ---------------------------------------------
    def test_listening_scanner_on_middle_rows(self):
        rows = {r for r, _ in lit(self.frame("listening"))}
        self.assertEqual(rows, {3, 4})

    def test_speaking_wave_uses_more_than_middle_rows(self):
        rows = {r for r, _ in lit(self.frame("speaking"))}
        self.assertTrue(rows - {3, 4})

    # --- Emotions ---------------------------------------------------------------
    def test_every_emotion_is_drawn_exactly_as_its_bitmap(self):
        self.assertEqual(len(self.bitmaps), 8)
        for name, pixels in self.bitmaps.items():
            with self.subTest(emotion=name):
                f = self.frame(f"emotion_{name}")
                self.assertEqual(lit(f), pixels)
                self.assertTrue(all(f[r][c] >= 4 for r, c in pixels), "emotions should be bright")

    def test_emotions_are_distinct(self):
        shapes = [frozenset(p) for p in self.bitmaps.values()]
        self.assertEqual(len(shapes), len(set(shapes)))

    def test_emotion_stays_while_processing_and_clears_when_listening(self):
        self.assertEqual(lit(self.frame("emotion_during_processing")), self.bitmaps["heart"])
        self.assertEqual({r for r, _ in lit(self.frame("emotion_cleared_by_listening"))}, {3, 4})

    def test_emotion_clears_when_idle(self):
        idle = lit(self.frame("emotion_cleared_by_idle"))
        self.assertIn(idle, [self.open_eyes(offset) for offset in (-1, 0, 1)], "expected the idle eyes, not the heart")

    def test_invalid_emotion_is_ignored(self):
        f = lit(self.frame("emotion_invalid"))
        self.assertNotIn(f, list(self.bitmaps.values()))
        self.assertTrue(f, "speaking wave expected")


if __name__ == "__main__":
    unittest.main()
