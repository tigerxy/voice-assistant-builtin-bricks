"""Feature tests for python/main.py, run against fake bricks (see fakes.py).

Run from the repository root:
    python3 -m unittest discover -s tests -v
"""

import importlib.util
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
import fakes  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
MAIN = ROOT / "python" / "main.py"
SKETCH = ROOT / "sketch" / "sketch.ino"
APP_YAML = ROOT / "app.yaml"


def load_main():
    """Import a fresh copy of main.py with all bricks faked."""
    fakes.install()
    fakes.reset()
    spec = importlib.util.spec_from_file_location("assistant_main", MAIN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AssistantTestCase(unittest.TestCase):
    def setUp(self):
        # No real waiting: every time.sleep returns at once (and is recorded)
        self.sleep = mock.patch("time.sleep").start()
        self.addCleanup(mock.patch.stopall)
        self.m = load_main()
        self.w = fakes.world
        self.w.events.clear()  # ignore start-up events unless a test needs them
        self.m.THINKING_FILLER_SECONDS = 5.0  # no thinking filler unless a test asks for it

    # helpers ---------------------------------------------------------------
    def wake(self):
        self.w.keyword_callbacks["hey_arduino"]()

    def run_until_idle(self, max_steps=30):
        for _ in range(max_steps):
            self.m.loop()
            if self.m.app_state == "IDLE":
                return
        self.fail(f"assistant did not return to IDLE (state={self.m.app_state})")

    def say(self, *utterances):
        self.w.utterances.extend(utterances)

    def answer_with(self, *chunks):
        self.w.llm_responder = lambda prompt, tools: iter(chunks)


# ---------------------------------------------------------------------------
class TestWakeWordAndListening(AssistantTestCase):
    def test_wake_word_is_builtin_hey_arduino(self):
        self.assertIn("hey_arduino", self.w.keyword_callbacks)
        self.assertNotIn("Ventuno", self.w.keyword_callbacks)

    def test_wake_word_only_starts_listening_when_idle(self):
        self.wake()
        self.assertEqual(self.m.app_state, "LISTENING")
        self.m.app_state = "PROCESSING"
        self.wake()
        self.assertEqual(self.m.app_state, "PROCESSING")

    def test_wake_chime_plays_before_microphone_opens(self):
        self.say("")
        self.wake()
        self.m.loop()
        first_tone = self.w.index(lambda e: e[0] == "tone")
        listen = self.w.index(lambda e: e[0] == "listen")
        self.assertNotEqual(first_tone, -1, "no wake chime")
        self.assertLess(first_tone, listen)
        self.assertEqual([e[1] for e in self.w.of("tone")][:2], [n for n, _ in self.m.EARCONS["wake"]])

    def test_listening_sets_led_scanner(self):
        self.say("")
        self.wake()
        self.m.loop()
        self.assertIn(("bridge", "set_state", self.m.LISTENING), self.w.events)

    def test_first_listen_uses_command_duration(self):
        self.say("")
        self.wake()
        self.m.loop()
        self.assertEqual(self.w.of("listen")[0][1], self.m.COMMAND_SECONDS)


class TestMicrophone(AssistantTestCase):
    def test_only_one_microphone_is_created(self):
        self.assertEqual(len(fakes.Microphone.instances), 1)

    def test_wake_word_and_speech_recognition_share_it(self):
        mic = fakes.Microphone.instances[0]
        self.assertIs(fakes.KeywordSpotting.instances[0].mic, mic)
        self.assertIs(self.m.asr.mic, mic)

    def test_microphone_is_listening_for_the_wake_word_at_start(self):
        # (The fake App.run() returns at once, so main.py's shutdown code already ran:
        # check the start-up events instead of the current state.)
        self.m = load_main()
        self.w = fakes.world
        self.assertEqual(self.w.of("mic")[0], ("mic", "start"))
        self.assertTrue(fakes.KeywordSpotting.instances[0].running)

    def test_wake_word_detector_is_paused_whenever_speech_is_recognized(self):
        self.say("What is two plus two?", "And three plus three?", "")
        self.wake()
        self.run_until_idle()
        listens = self.w.of("listen")
        self.assertEqual(len(listens), 3)
        for _, _, mic_on, kws_on in listens:
            self.assertTrue(mic_on, "microphone must be on while recognizing speech")
            self.assertFalse(kws_on, "wake word detector must not read the microphone at the same time")

    def test_wake_word_detector_resumes_after_the_conversation(self):
        self.say("Hello there", "")
        self.wake()
        self.run_until_idle()
        self.assertEqual(
            [e[1] for e in self.w.of("app")], ["stop_brick", "start_brick"],
            "pause once at the start, resume once at the end",
        )
        self.assertTrue(fakes.KeywordSpotting.instances[0].running)
        self.assertTrue(fakes.Microphone.instances[0].started)

    def test_microphone_is_off_while_the_assistant_speaks(self):
        self.say("Tell me something", "")
        self.answer_with("Here you go.")
        self.wake()
        self.run_until_idle()
        speak = self.w.index(lambda e: e == ("speak", "Here you go.", False))
        last_mic = [e for e in self.w.events[:speak] if e[0] == "mic"][-1]
        self.assertEqual(last_mic, ("mic", "stop"))


class TestConversation(AssistantTestCase):
    def test_answer_is_spoken_and_conversation_continues_without_wake_word(self):
        self.say("What is the capital of France?", "And of Italy?", "")
        self.w.llm_responder = lambda prompt, tools: iter(
            ["Paris."] if "France" in prompt else ["Rome."]
        )
        self.wake()
        self.run_until_idle()

        self.assertEqual(len(self.w.of("llm")), 2, "follow-up question was not processed")
        self.assertIn("Paris.", self.w.spoken())
        self.assertIn("Rome.", self.w.spoken())
        listens = [e[1] for e in self.w.of("listen")]
        self.assertEqual(listens, [self.m.COMMAND_SECONDS, self.m.FOLLOW_UP_SECONDS, self.m.FOLLOW_UP_SECONDS])

    def test_streamed_answer_is_spoken_sentence_by_sentence_without_blocking(self):
        self.say("How are you?", "")
        self.answer_with("I'm great. Thanks", " for asking! What", " about you?")
        self.wake()
        self.run_until_idle()
        answer = [(e[1], e[2]) for e in self.w.of("speak")]
        self.assertEqual(
            answer[:3],
            [("I'm great.", False), ("Thanks for asking!", False), ("What about you?", False)],
        )

    def test_speaking_state_is_set_before_first_sentence(self):
        self.say("Hi there, tell me a joke", "")
        self.answer_with("Why not.")
        self.wake()
        self.run_until_idle()
        speaking = self.w.index(lambda e: e == ("bridge", "set_state", self.m.SPEAKING))
        first_speak = self.w.index(lambda e: e[0] == "speak")
        self.assertLess(speaking, first_speak)

    def test_prompt_contains_local_time_and_part_of_day(self):
        self.say("What time is it?", "")
        self.wake()
        self.run_until_idle()
        prompt = self.w.of("llm")[0][1]
        self.assertIn("current local time and date", prompt)
        self.assertRegex(prompt, r"\((morning|afternoon|evening|night)\)")
        self.assertIn("User: What time is it?", prompt)

    def test_silence_in_follow_up_ends_quietly_and_clears_memory(self):
        self.say("Tell me something", "")
        self.wake()
        self.run_until_idle()
        self.assertFalse(any(t in self.m.DIDNT_CATCH for t in self.w.spoken()))
        self.assertIn(("clear_memory",), self.w.events)
        tones = [e[1] for e in self.w.of("tone")]
        self.assertEqual(tones[-2:], [n for n, _ in self.m.EARCONS["end"]], "no end chime")
        self.assertEqual(self.w.bridge("set_state")[-1][2], self.m.IDLE)

    def test_silence_right_after_wake_word_says_didnt_catch_that(self):
        self.say("")
        self.wake()
        self.run_until_idle()
        self.assertTrue(any(t in self.m.DIDNT_CATCH for t in self.w.spoken()))
        self.assertEqual(self.w.of("llm"), [])

    def test_exit_phrase_says_farewell_and_ends(self):
        self.say("Tell me a fact", "Okay, bye!")
        self.wake()
        self.run_until_idle()
        self.assertEqual(len(self.w.of("llm")), 1, "exit phrase must not go to the LLM")
        farewells = ["Good night", "talk to you later", "call me", "See you"]
        self.assertTrue(any(any(f in t for f in farewells) for t in self.w.spoken()))
        self.assertIn(("clear_memory",), self.w.events)

    def test_exit_phrase_detection(self):
        stop = self.m.wants_to_stop
        for text in ["Stop.", "Goodbye!", "Okay, bye.", "Never mind.", "That's all"]:
            self.assertTrue(stop(text), text)
        for text in ["What about the bus stop near me?", "Tell me about Berlin", "Buy milk"]:
            self.assertFalse(stop(text), text)

    def test_conversation_memory_is_enabled(self):
        self.assertEqual(fakes.LargeLanguageModel.last.memory, self.m.MEMORY_MESSAGES)
        self.assertGreaterEqual(self.m.MEMORY_MESSAGES, 6)


class TestNaturalReactions(AssistantTestCase):
    def test_thanks_gets_instant_reply_without_llm(self):
        self.say("Thank you so much!", "")
        self.wake()
        self.run_until_idle()
        self.assertEqual(self.w.of("llm"), [])
        self.assertTrue(any(t in self.m.THANKS_REPLIES for t in self.w.spoken()))
        self.assertIn(("bridge", "show_emotion", self.m.EMOTIONS.index("happy")), self.w.events)

    def test_greeting_gets_time_aware_reply_and_conversation_continues(self):
        self.say("Hello!", "What's two plus two?", "")
        self.answer_with("Four.")
        self.wake()
        self.run_until_idle()
        self.assertEqual(len(self.w.of("llm")), 1)
        greeting = self.w.spoken()[0]
        self.assertTrue(re.search(r"Good (morning|afternoon|evening)|Hi!|Hey|night owl|up late", greeting), greeting)

    def test_quick_reply_patterns(self):
        n = self.m.normalize
        for text in ["Thanks!", "Okay, thanks.", "Thank you very much.", "Thanks, Arduino!"]:
            self.assertTrue(self.m.THANKS_RE.match(n(text)), text)
        for text in ["Hello!", "Good morning.", "Hey there"]:
            self.assertTrue(self.m.GREETING_RE.match(n(text)), text)
        for text in ["Thank you for the info about Rome", "Hello, what's the weather?"]:
            self.assertFalse(self.m.THANKS_RE.match(n(text)) or self.m.GREETING_RE.match(n(text)), text)

    def test_greeting_and_farewell_depend_on_time_of_day(self):
        with mock.patch.object(self.m, "part_of_day", return_value="morning"):
            self.assertTrue(any("morning" in self.m.greeting_reply() or "Hey" in self.m.greeting_reply() for _ in range(20)))
        with mock.patch.object(self.m, "part_of_day", return_value="night"):
            self.assertTrue(all("late" in g or "night owl" in g for g in (self.m.greeting_reply() for _ in range(20))))
        with mock.patch.object(self.m, "is_night", return_value=True):
            self.assertTrue(all("night" in self.m.farewell().lower() for _ in range(20)))

    def test_part_of_day_and_night_hours(self):
        pod = self.m.part_of_day
        self.assertEqual([pod(h) for h in (6, 13, 19, 23, 3)], ["morning", "afternoon", "evening", "night", "night"])
        self.assertTrue(self.m.is_night(23))
        self.assertFalse(self.m.is_night(12))

    def test_error_leads_to_spoken_apology_and_error_sound(self):
        def broken(prompt, tools):
            raise RuntimeError("NPU fell asleep")
            yield  # pragma: no cover

        self.w.llm_responder = broken
        self.say("Tell me something")
        self.wake()
        self.run_until_idle()
        self.assertTrue(any(t in self.m.ERROR_REPLIES for t in self.w.spoken()))
        tones = [e[1] for e in self.w.of("tone")]
        error = [n for n, _ in self.m.EARCONS["error"]]
        self.assertTrue(any(tones[i:i + len(error)] == error for i in range(len(tones))), "no error sound")
        self.assertEqual(self.m.app_state, "IDLE")


class TestFillerWords(AssistantTestCase):
    def test_thinking_filler_when_llm_is_slow(self):
        self.m.THINKING_FILLER_SECONDS = 0.05

        def slow(prompt, tools):
            fakes.wait(0.4)
            yield "Here is my answer."

        self.w.llm_responder = slow
        self.say("A hard question", "")
        self.wake()
        self.run_until_idle()
        spoken = self.w.spoken()
        self.assertIn(spoken[0], self.m.THINKING_FILLERS)
        self.assertIn("Here is my answer.", spoken[1:])

    def test_no_thinking_filler_when_llm_is_fast(self):
        self.m.THINKING_FILLER_SECONDS = 0.5
        self.say("An easy question", "")
        self.answer_with("Easy.")
        self.wake()
        self.run_until_idle()
        fakes.wait(0.6)  # give a wrongly running timer the chance to fire
        self.assertFalse(any(t in self.m.THINKING_FILLERS for t in self.w.spoken()))

    def test_search_filler_is_spoken_before_the_answer(self):
        self.w.http_routes = {"https://api.duckduckgo.com/": {"AbstractText": "Mount Everest is 8849 m."}}

        def uses_search(prompt, tools):
            result = tools["web_search"]("Mount Everest height")
            assert "8849" in result
            yield "It is about eight thousand eight hundred meters."

        self.w.llm_responder = uses_search
        self.say("How high is Mount Everest?", "")
        self.wake()
        self.run_until_idle()
        spoken = self.w.spoken()
        self.assertIn(spoken[0], self.m.SEARCH_FILLERS)
        self.assertTrue(spoken[1].startswith("It is about"))
        self.assertFalse(any(t in self.m.THINKING_FILLERS for t in spoken), "thinking filler on top of search filler")

    def test_weather_filler(self):
        self.m.get_weather("Berlin")
        self.assertIn(self.w.spoken()[0], self.m.WEATHER_FILLERS)


class TestTools(AssistantTestCase):
    def test_tools_are_registered_with_the_llm(self):
        self.assertEqual(set(fakes.LargeLanguageModel.last.tools), {"web_search", "get_weather", "show_emotion"})

    def test_tools_have_docstrings_for_the_llm(self):
        for tool in (self.m.web_search, self.m.get_weather, self.m.show_emotion):
            self.assertTrue(tool.__doc__ and "Args:" in tool.__doc__, tool.__name__)

    def test_system_prompt_mentions_every_tool(self):
        for name in ("web_search", "get_weather", "show_emotion"):
            self.assertIn(name, self.m.SYSTEM_PROMPT)

    def test_web_search_combines_duckduckgo_and_wikipedia(self):
        self.w.http_routes = {
            "https://api.duckduckgo.com/": {"Answer": "", "AbstractText": "DDG says hi.", "RelatedTopics": [{"Text": "Topic A"}]},
            "https://en.wikipedia.org/w/api.php": {"query": {"search": [{"title": "Arduino"}]}},
            "https://en.wikipedia.org/api/rest_v1/page/summary/": {"extract": "Arduino is an open-source platform."},
        }
        result = self.m.web_search("Arduino")
        self.assertIn("DDG says hi.", result)
        self.assertIn("Topic A", result)
        self.assertIn("Arduino: Arduino is an open-source platform.", result)

    def test_web_search_result_is_capped_for_the_local_model(self):
        self.w.http_routes = {"https://api.duckduckgo.com/": {"AbstractText": "x" * 10000}}
        self.assertLessEqual(len(self.m.web_search("long")), 2000)

    def test_web_search_survives_network_errors(self):
        self.w.http_routes = {"https://": ConnectionError("offline")}
        self.assertEqual(self.m.web_search("anything"), "No results found.")

    def test_get_weather_uses_builtin_brick_with_clamped_days(self):
        result = self.m.get_weather("Berlin", days_ahead=10)
        city, kwargs = self.w.of("weather")[0][1:]
        self.assertEqual(city, "Berlin")
        self.assertEqual(kwargs["forecast_days"], 7)
        self.assertEqual(kwargs["timezone"], str(self.m.TIME_ZONE))
        self.assertIn("Slight rain", result)

    def test_show_emotion_draws_symbol_and_plays_jingle(self):
        result = self.m.show_emotion("Heart")
        self.assertIn(("bridge", "show_emotion", 0), self.w.events)
        self.assertEqual([e[1] for e in self.w.of("tone")], [n for n, _ in self.m.EARCONS["heart"]])
        self.assertTrue(self.m.emotion_shown)
        self.assertIn("heart", result)

    def test_show_emotion_rejects_unknown_symbols(self):
        result = self.m.show_emotion("dancing")
        self.assertEqual(self.w.bridge("show_emotion"), [])
        self.assertIn("Unknown emotion", result)

    def test_every_emotion_has_a_jingle(self):
        for name in self.m.EMOTIONS:
            self.assertIn(name, self.m.EARCONS, name)

    def test_emotion_stays_visible_after_speaking(self):
        def loving(prompt, tools):
            tools["show_emotion"]("heart")
            yield "Yes, I do!"

        self.w.llm_responder = loving
        self.say("Do you like me?", "")
        self.wake()
        self.run_until_idle()
        self.assertIn(("bridge", "show_emotion", 0), self.w.events)
        self.assertIn("Yes, I do!", self.w.spoken())
        self.assertIn(mock.call(self.m.EMOTION_HOLD_SECONDS), self.sleep.call_args_list)


class TestIdleFace(AssistantTestCase):
    def idle_modes(self):
        return [e[2] for e in self.w.bridge("set_idle_mode")]

    def test_starts_awake(self):
        self.m = load_main()
        self.w = fakes.world
        self.assertEqual(self.idle_modes(), [self.m.IDLE_AWAKE])

    def test_falls_asleep_after_inactivity(self):
        self.m.last_activity -= self.m.SLEEP_AFTER_SECONDS - 10
        self.m.loop()
        self.assertEqual(self.idle_modes(), [], "fell asleep too early")
        self.m.last_activity -= 20
        self.m.loop()
        self.assertEqual(self.idle_modes(), [self.m.IDLE_SLEEPING])
        self.m.loop()
        self.assertEqual(self.idle_modes(), [self.m.IDLE_SLEEPING], "mode should only be sent when it changes")

    def test_default_sleep_delay_is_five_minutes(self):
        self.assertEqual(self.m.SLEEP_AFTER_SECONDS, 300)

    def test_wakes_up_after_a_conversation(self):
        self.m.last_activity -= self.m.SLEEP_AFTER_SECONDS + 1
        self.m.loop()
        self.say("Hello there", "")
        self.wake()
        self.run_until_idle()
        self.assertEqual(self.idle_modes(), [self.m.IDLE_SLEEPING, self.m.IDLE_AWAKE])

    def test_off_and_awake_settings(self):
        self.m.IDLE_FACE = "off"
        self.m.update_idle_face()
        self.m.IDLE_FACE = "awake"
        self.m.last_activity -= 10_000
        self.m.update_idle_face()
        self.assertEqual(self.idle_modes(), [self.m.IDLE_OFF, self.m.IDLE_AWAKE])


class TestSoundEffects(AssistantTestCase):
    def test_sounds_can_be_switched_off(self):
        self.m.SOUND_EFFECTS = False
        self.m.play_earcon("wake")
        self.m.show_emotion("heart")
        self.assertEqual(self.w.of("tone"), [])

    def test_broken_sound_never_breaks_the_conversation(self):
        self.m.sfx.play_tone = mock.Mock(side_effect=OSError("speaker busy"))
        self.say("Tell me something", "")
        self.answer_with("Here you go.")
        self.wake()
        self.run_until_idle()
        self.assertIn("Here you go.", self.w.spoken())

    def test_all_earcon_notes_are_valid(self):
        note = re.compile(r"^(REST|[A-G](#|B)?[0-7])$")
        for name, notes in self.m.EARCONS.items():
            for n, seconds in notes:
                self.assertRegex(n, note, name)
                self.assertGreater(seconds, 0)
                self.assertLessEqual(seconds, 0.5, f"{name} is too long for a chime")


class TestProjectConsistency(unittest.TestCase):
    """Static checks across app.yaml, main.py and sketch.ino."""

    @classmethod
    def setUpClass(cls):
        cls.main = MAIN.read_text()
        cls.sketch = SKETCH.read_text()
        cls.app = APP_YAML.read_text()

    def test_only_builtin_bricks(self):
        bricks = re.findall(r"^- ([\w:]+):", self.app, re.M)
        self.assertTrue(bricks)
        for brick in bricks:
            self.assertTrue(brick.startswith("arduino:"), f"{brick} is not a built-in brick")
        self.assertFalse((ROOT / "bricks").exists(), "custom bricks folder must not exist")
        self.assertNotIn("ei-model", self.app, "custom Edge Impulse model in app.yaml")

    def test_every_used_brick_is_declared_in_app_yaml(self):
        used = set(re.findall(r"from arduino\.app_bricks\.(\w+) import", self.main))
        declared = set(re.findall(r"^- arduino:(\w+):", self.app, re.M))
        self.assertEqual(used - declared, set())

    def test_python_only_calls_rpcs_the_sketch_provides(self):
        called = set(re.findall(r'Bridge\.call\("(\w+)"', self.main))
        provided = set(re.findall(r'Bridge\.provide\("(\w+)"', self.sketch))
        self.assertEqual(called - provided, set())

    def test_emotions_match_sketch_bitmaps(self):
        names = re.findall(r"\{ // (\d+): (\w+)", self.sketch)  # e.g. "{ // 0: heart"
        python_order = re.search(r"EMOTIONS = \[(.*?)\]", self.main).group(1)
        python_names = re.findall(r'"(\w+)"', python_order)
        self.assertEqual([n for _, n in names], python_names)
        self.assertEqual(int(re.search(r"NUM_EMOTIONS = (\d+)", self.sketch).group(1)), len(python_names))

    def test_license_and_credit(self):
        self.assertIn("GNU GENERAL PUBLIC LICENSE", (ROOT / "LICENSE").read_text())
        self.assertIn("mcmchris/keyword-asr-local-llm-kokoro-tts", (ROOT / "README.md").read_text())


if __name__ == "__main__":
    unittest.main()
