"""Feature tests for the assistant (python/), run against fake bricks (see fakes.py).

Run from the repository root:
    python3 -m unittest discover -s tests -b -v
"""

import re
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
PYTHON = ROOT / "python"
SKETCH = ROOT / "sketch" / "sketch.ino"
APP_YAML = ROOT / "app.yaml"

sys.path.insert(0, str(TESTS))
import fakes  # noqa: E402

fakes.install()
sys.path.insert(0, str(PYTHON))
import config  # noqa: E402
from assistant import conversation as conversation_module  # noqa: E402
from assistant import face as face_module  # noqa: E402
from assistant import setup as setup_module  # noqa: E402
from assistant import sounds as sounds_module  # noqa: E402
from assistant import textcalls  # noqa: E402
from assistant.timeofday import is_night, part_of_day  # noqa: E402
from languages import load_language  # noqa: E402

IDLE_STATE = conversation_module.IDLE
EMOTIONS = face_module.EMOTIONS
EARCONS = sounds_module.EARCONS


class AssistantTestCase(unittest.TestCase):
    language = "en"

    def setUp(self):
        # No real waiting: every time.sleep returns at once (and is recorded)
        self.sleep = mock.patch("time.sleep").start()
        self.addCleanup(mock.patch.stopall)
        fakes.reset()
        self.w = fakes.world
        # no thinking filler unless a test asks for it
        self.settings = config.Settings(
            language=self.language,
            thinking_filler_seconds=5.0,
            # short real waits, so silence doesn't slow the tests down
            wait_for_speech_seconds=0.05,
            follow_up_wait_seconds=0.05,
        )
        self.c = setup_module.build_assistant(self.settings)
        self.lang = self.c.language
        self.brain = self.c.brain
        self.expression = self.c.expression
        self.tools = self.brain.tools
        self.startup_events = list(self.w.events)
        self.w.events.clear()  # ignore start-up events unless a test needs them

    # helpers ---------------------------------------------------------------
    def wake(self):
        self.w.keyword_callbacks["hey_arduino"]()

    def run_until_idle(self, max_steps=30):
        for _ in range(max_steps):
            self.c.step()
            if self.c.state == IDLE_STATE:
                return
        self.fail(f"assistant did not return to IDLE (state={self.c.state})")

    def say(self, *utterances):
        self.w.utterances.extend(utterances)

    def answer_with(self, *chunks):
        self.w.llm_responder = lambda prompt, tools: iter(chunks)

    def set_time(self, hour):
        when = datetime(2026, 10, 7, hour, 30, tzinfo=self.settings.time_zone)
        self.brain.now = self.c.now = lambda: when


# ---------------------------------------------------------------------------
class TestWakeWordAndListening(AssistantTestCase):
    def test_wake_word_is_builtin_hey_arduino(self):
        self.assertIn("hey_arduino", self.w.keyword_callbacks)
        self.assertNotIn("Ventuno", self.w.keyword_callbacks)

    def test_wake_word_only_starts_listening_when_idle(self):
        self.wake()
        self.assertEqual(self.c.state, "LISTENING")
        self.c.state = "THINKING"
        self.wake()
        self.assertEqual(self.c.state, "THINKING")

    def test_wake_chime_plays_before_microphone_opens(self):
        self.say("")
        self.wake()
        self.c.step()
        first_tone = self.w.index(lambda e: e[0] == "tone")
        listen = self.w.index(lambda e: e[0] == "listen")
        self.assertNotEqual(first_tone, -1, "no wake chime")
        self.assertLess(first_tone, listen)
        self.assertEqual([e[1] for e in self.w.of("tone")][:2], [n for n, _ in EARCONS["wake"]])

    def test_listening_sets_led_scanner(self):
        self.say("")
        self.wake()
        self.c.step()
        self.assertIn(("bridge", "set_state", face_module.LISTENING), self.w.events)



class TestListeningWithVAD(AssistantTestCase):
    """No fixed recording length: the speech service's VAD ends each sentence."""

    def listen_waits(self):
        listen = mock.patch.object(self.c.ears, "listen", wraps=self.c.ears.listen).start()
        return listen

    def test_sentence_ends_with_the_vad_not_a_fixed_duration(self):
        # The fake ASR fails on transcribe_stream(duration): only the VAD-based stream may be used
        self.say("What is the capital of France?", "")
        self.answer_with("Paris.")
        self.wake()
        self.run_until_idle()
        timeout, mic_on, vad_ms = self.w.of("listen")[0][1:]
        self.assertEqual(timeout, self.settings.max_sentence_seconds)
        self.assertTrue(mic_on)
        self.assertEqual(vad_ms, self.settings.end_of_speech_ms)
        self.assertEqual(self.w.of("llm")[0][1].split("User: ")[-1], "What is the capital of France?")

    def test_noise_segments_do_not_end_the_sentence(self):
        # the fake ASR sends an empty full_text before the real one
        self.say("Hello there, how are you", "")
        self.answer_with("Fine.")
        self.wake()
        self.run_until_idle()
        self.assertIn("how are you", self.w.of("llm")[0][1])

    def test_gives_up_when_nobody_starts_talking(self):
        self.say("")
        self.wake()
        self.c.step()
        self.assertIn(("asr_cancel",), self.w.events)

    def test_waits_for_someone_who_starts_talking_a_little_late(self):
        self.settings.wait_for_speech_seconds = 1.0
        self.say(fakes.Speech("Tell me a joke", delay=0.1), "")
        self.answer_with("Why not.")
        self.wake()
        self.c.step()
        self.assertNotIn(("asr_cancel",), self.w.events)
        self.assertEqual(self.c.user_text, "Tell me a joke")

    def test_long_sentence_is_not_cut_off(self):
        # starts in time, but takes much longer than the wait time to finish
        self.settings.wait_for_speech_seconds = 0.1
        self.say(fakes.Speech("Tell me everything about the history of Rome", delay=0.02, duration=0.4), "")
        self.answer_with("Okay.")
        self.wake()
        self.c.step()
        self.assertNotIn(("asr_cancel",), self.w.events)
        self.assertEqual(self.c.user_text, "Tell me everything about the history of Rome")

    def test_too_late_counts_as_silence(self):
        self.settings.wait_for_speech_seconds = 0.05
        self.say(fakes.Speech("Hello?", delay=1.0))
        self.wake()
        self.run_until_idle()
        self.assertIn(("asr_cancel",), self.w.events)
        self.assertTrue(any(t in self.lang.didnt_catch for t in self.w.spoken()))

    def test_wait_times_after_wake_word_and_after_an_answer(self):
        self.settings.follow_up_wait_seconds = 0.06  # different from wait_for_speech_seconds
        listen = self.listen_waits()
        self.say("What is the capital of France?", "And of Italy?", "")
        self.answer_with("Rome.")
        self.wake()
        self.run_until_idle()
        s = self.settings
        self.assertEqual([c.args[0] for c in listen.call_args_list],
                         [s.wait_for_speech_seconds, s.follow_up_wait_seconds, s.follow_up_wait_seconds])

    def test_defaults(self):
        s = config.Settings()
        self.assertGreater(s.end_of_speech_ms, 700, "a bit longer than the service default, for short pauses")
        self.assertGreater(s.max_sentence_seconds, s.wait_for_speech_seconds)


class TestMicrophone(AssistantTestCase):
    def test_code_creates_one_microphone(self):
        sources = "".join(p.read_text() for p in PYTHON.rglob("*.py"))
        self.assertEqual(sources.count("Microphone("), 1)

    def test_wake_word_and_speech_recognition_use_separate_streams(self):
        # One Microphone object must never be read by two bricks at once:
        # every read takes the chunk away from the other reader.
        kws_mic = fakes.KeywordSpotting.instances[0].mic
        self.assertIsNot(kws_mic, self.c.ears.asr.mic)
        self.assertIs(self.c.ears.asr.mic, self.c.ears.mic)

    def test_wake_word_detector_is_never_stopped_by_the_app(self):
        # Stopping it while its reader thread runs crashes that thread on the board
        # ("Attempted to read from E20 before starting it") and blocks for 5 seconds.
        self.say("What is two plus two?", "And three plus three?", "")
        self.wake()
        self.run_until_idle()
        self.assertEqual(self.w.of("app"), [])
        self.assertFalse([e for e in self.w.of("mic") if e[2] == "kws"])

    def test_speech_recognition_microphone_is_on_while_listening(self):
        self.say("What is two plus two?", "And three plus three?", "")
        self.wake()
        self.run_until_idle()
        listens = self.w.of("listen")
        self.assertEqual(len(listens), 3)
        self.assertTrue(all(mic_on for _, _, mic_on, _ in listens))

    def test_microphone_is_off_while_the_assistant_speaks(self):
        self.say("Tell me something", "")
        self.answer_with("Here you go.")
        self.wake()
        self.run_until_idle()
        speak = self.w.index(lambda e: e == ("speak", "Here you go."))
        last_mic = [e for e in self.w.events[:speak] if e[0] == "mic" and e[2] == "asr"][-1]
        self.assertEqual(last_mic, ("mic", "stop", "asr"))


class TestSpeechWithBoardLibrary(AssistantTestCase):
    """The TTS brick on the board only has a blocking speak(text)."""

    def test_answer_streams_while_llm_is_still_generating(self):
        def slow_answer(prompt, tools):
            yield "First sentence."
            yield " Second"  # the space after the "." shows the first sentence is complete
            fakes.wait(0.2)
            spoken_before_llm_finished.extend(self.w.spoken())
            yield " sentence."

        spoken_before_llm_finished = []
        self.w.llm_responder = slow_answer
        self.say("Talk to me", "")
        self.wake()
        self.run_until_idle()
        self.assertEqual(spoken_before_llm_finished, ["First sentence."])
        self.assertEqual(self.w.spoken()[:2], ["First sentence.", "Second sentence."])

    def test_never_speaks_twice_at_the_same_time(self):
        self.settings.thinking_filler_seconds = 0.01

        def slow(prompt, tools):
            fakes.wait(0.1)
            if prompt.startswith("[Results"):
                yield "One. Two. Three. Four."
            else:
                yield 'web_search("anything")'

        self.w.http_routes = {"https://": ConnectionError("offline")}
        self.w.llm_responder = slow
        self.say("Question", "")
        self.wake()
        self.run_until_idle()
        self.assertEqual(self.w.of("tts_busy"), [])
        self.assertEqual(self.w.spoken()[-4:], ["One.", "Two.", "Three.", "Four."])

    def test_waits_until_everything_is_spoken_before_listening_again(self):
        self.say("Tell me a story", "")
        self.answer_with("Once upon a time. There was a robot. The end.")
        self.wake()
        self.run_until_idle()
        last_speak = max(i for i, e in enumerate(self.w.events) if e[0] == "speak")
        second_listen = [i for i, e in enumerate(self.w.events) if e[0] == "listen"][1]
        self.assertLess(last_speak, second_listen)

    def test_stop_talking_drops_queued_sentences(self):
        self.expression.voice.stop_talking()
        self.assertIn(("tts_cancel",), self.w.events)
        self.assertFalse(self.expression.voice.is_busy())


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
        self.assertEqual(len(self.w.of("listen")), 3)

    def test_streamed_answer_is_spoken_sentence_by_sentence(self):
        self.say("How are you?", "")
        self.answer_with("I'm great. Thanks", " for asking! What", " about you?")
        self.wake()
        self.run_until_idle()
        self.assertEqual(self.w.spoken()[:3], ["I'm great.", "Thanks for asking!", "What about you?"])

    def test_speaking_state_is_set_before_first_sentence(self):
        self.say("Hi there, tell me a joke", "")
        self.answer_with("Why not.")
        self.wake()
        self.run_until_idle()
        speaking = self.w.index(lambda e: e == ("bridge", "set_state", face_module.SPEAKING))
        first_speak = self.w.index(lambda e: e[0] == "speak")
        self.assertLess(speaking, first_speak)

    def test_prompt_contains_local_time_and_part_of_day(self):
        self.set_time(9)
        self.say("What time is it?", "")
        self.wake()
        self.run_until_idle()
        prompt = self.w.of("llm")[0][1]
        self.assertIn("09:30 AM, Wednesday, October 7, 2026", prompt)
        self.assertIn("(morning)", prompt)
        self.assertIn("User: What time is it?", prompt)

    def test_silence_in_follow_up_ends_quietly_and_clears_memory(self):
        self.say("Tell me something", "")
        self.wake()
        self.run_until_idle()
        self.assertFalse(any(t in self.lang.didnt_catch for t in self.w.spoken()))
        self.assertIn(("clear_memory",), self.w.events)
        tones = [e[1] for e in self.w.of("tone")]
        self.assertEqual(tones[-2:], [n for n, _ in EARCONS["end"]], "no end chime")
        self.assertEqual(self.w.bridge("set_state")[-1][2], face_module.IDLE)

    def test_silence_right_after_wake_word_says_didnt_catch_that(self):
        self.say("")
        self.wake()
        self.run_until_idle()
        self.assertTrue(any(t in self.lang.didnt_catch for t in self.w.spoken()))
        self.assertEqual(self.w.of("llm"), [])

    def test_exit_phrase_says_farewell_and_ends(self):
        self.say("Tell me a fact", "Okay, bye!")
        self.wake()
        self.run_until_idle()
        self.assertEqual(len(self.w.of("llm")), 1, "exit phrase must not go to the LLM")
        all_farewells = [f for options in self.lang.farewells.values() for f in options]
        self.assertIn(self.w.spoken()[-1], all_farewells)
        self.assertIn(("clear_memory",), self.w.events)

    def test_exit_phrase_detection(self):
        for text in ["Stop.", "Goodbye!", "Okay, bye.", "Never mind.", "That's all"]:
            self.assertTrue(self.lang.is_exit(text), text)
        for text in ["What about the bus stop near me?", "Tell me about Berlin", "Buy milk"]:
            self.assertFalse(self.lang.is_exit(text), text)

    def test_conversation_memory_is_enabled(self):
        self.assertEqual(fakes.LargeLanguageModel.last.memory, self.settings.memory_messages)
        self.assertGreaterEqual(self.settings.memory_messages, 6)


class TestNaturalReactions(AssistantTestCase):
    def test_thanks_gets_instant_reply_without_llm(self):
        self.say("Thank you so much!", "")
        self.wake()
        self.run_until_idle()
        self.assertEqual(self.w.of("llm"), [])
        self.assertTrue(any(t in self.lang.thanks_replies for t in self.w.spoken()))
        self.assertIn(("bridge", "show_emotion", EMOTIONS.index("happy")), self.w.events)

    def test_greeting_gets_time_aware_reply_and_conversation_continues(self):
        self.set_time(9)
        self.say("Hello!", "What's two plus two?", "")
        self.answer_with("Four.")
        self.wake()
        self.run_until_idle()
        self.assertEqual(len(self.w.of("llm")), 1)
        self.assertIn(self.w.spoken()[0], self.lang.greetings["morning"])

    def test_quick_reply_patterns(self):
        for text in ["Thanks!", "Okay, thanks.", "Thank you very much.", "Thanks, Arduino!"]:
            self.assertTrue(self.lang.is_thanks(text), text)
        for text in ["Hello!", "Good morning.", "Hey there"]:
            self.assertTrue(self.lang.is_greeting(text), text)
        for text in ["Thank you for the info about Rome", "Hello, what's the weather?"]:
            self.assertFalse(self.lang.is_thanks(text) or self.lang.is_greeting(text), text)

    def test_farewell_at_night(self):
        self.set_time(23)
        self.say("Bye!")
        self.wake()
        self.run_until_idle()
        self.assertIn(self.w.spoken()[-1], self.lang.farewells["night"])

    def test_part_of_day_and_night_hours(self):
        self.assertEqual([part_of_day(h) for h in (6, 13, 19, 23, 3)],
                         ["morning", "afternoon", "evening", "night", "night"])
        self.assertTrue(is_night(23, (22, 7)))
        self.assertFalse(is_night(12, (22, 7)))

    def test_error_leads_to_spoken_apology_and_error_sound(self):
        def broken(prompt, tools):
            raise RuntimeError("NPU fell asleep")
            yield  # pragma: no cover

        self.w.llm_responder = broken
        self.say("Tell me something")
        self.wake()
        self.run_until_idle()
        self.assertTrue(any(t in self.lang.error_replies for t in self.w.spoken()))
        tones = [e[1] for e in self.w.of("tone")]
        error = [n for n, _ in EARCONS["error"]]
        self.assertTrue(any(tones[i:i + len(error)] == error for i in range(len(tones))), "no error sound")
        self.assertEqual(self.c.state, IDLE_STATE)


class TestFillerWords(AssistantTestCase):
    def test_thinking_filler_when_llm_is_slow(self):
        self.settings.thinking_filler_seconds = 0.05

        def slow(prompt, tools):
            fakes.wait(0.4)
            yield "Here is my answer."

        self.w.llm_responder = slow
        self.say("A hard question", "")
        self.wake()
        self.run_until_idle()
        spoken = self.w.spoken()
        self.assertIn(spoken[0], self.lang.thinking_fillers)
        self.assertIn("Here is my answer.", spoken[1:])

    def test_no_thinking_filler_when_llm_is_fast(self):
        self.settings.thinking_filler_seconds = 0.5
        self.say("An easy question", "")
        self.answer_with("Easy.")
        self.wake()
        self.run_until_idle()
        fakes.wait(0.6)  # give a wrongly running timer the chance to fire
        self.assertFalse(any(t in self.lang.thinking_fillers for t in self.w.spoken()))

    def test_search_filler_is_spoken_before_the_answer(self):
        self.w.http_routes = {"https://api.duckduckgo.com/": {"AbstractText": "Mount Everest is 8849 m."}}

        def uses_search(prompt, tools):
            if prompt.startswith("[Results"):
                assert "8849" in prompt
                yield "It is about eight thousand eight hundred meters."
            else:
                yield 'web_search("Mount Everest height")'

        self.w.llm_responder = uses_search
        self.say("How high is Mount Everest?", "")
        self.wake()
        self.run_until_idle()
        spoken = self.w.spoken()
        self.assertIn(spoken[0], self.lang.search_fillers)
        self.assertTrue(spoken[1].startswith("It is about"))
        self.assertFalse(any(t in self.lang.thinking_fillers for t in spoken), "thinking filler on top of search filler")

    def test_weather_filler(self):
        self.tools.run("get_weather", {"city": "Berlin"})
        self.expression.voice.wait()
        self.assertIn(self.w.spoken()[0], self.lang.weather_fillers)

    def test_no_filler_once_something_was_said(self):
        self.expression.say("Let me check that.")
        self.tools.run("get_weather", {"city": "Berlin"})
        self.expression.voice.wait()
        self.assertEqual(self.w.spoken(), ["Let me check that."])


class TestTools(AssistantTestCase):
    def test_tools_are_registered_with_the_llm(self):
        tools = fakes.LargeLanguageModel.last.tools
        self.assertEqual(set(tools), {"web_search", "get_weather", "show_emotion"})
        self.assertIs(tools["web_search"], self.tools.web_search)

    def test_no_tools_registered_when_switched_off(self):
        fakes.reset()
        setup_module.build_assistant(config.Settings(use_tools=False))
        self.assertEqual(fakes.LargeLanguageModel.last.tools, {})
        self.assertNotIn("web_search", fakes.LargeLanguageModel.last.system_prompt)

    def test_structured_tool_calls_run_through_the_brick(self):
        # When the runner returns a real tool call, the brick runs the function and then
        # streams the rest of the answer. Simulated here by calling the registered tool.
        self.w.http_routes = {"https://api.duckduckgo.com/": {"AbstractText": "Mount Everest is 8849 m high."}}

        def brick_with_tool_calls(prompt, tools):
            tools["show_emotion"]("surprised")
            result = tools["web_search"]("Mount Everest height")
            assert "8849" in result
            yield "Wow, it's about eight thousand eight hundred meters high!"

        self.w.llm_responder = brick_with_tool_calls
        self.say("How high is Mount Everest?", "")
        self.wake()
        self.run_until_idle()
        spoken = self.w.spoken()
        self.assertIn(spoken[0], self.lang.search_fillers)
        self.assertEqual(spoken[1], "Wow, it's about eight thousand eight hundred meters high!")
        self.assertIn(("bridge", "show_emotion", EMOTIONS.index("surprised")), self.w.events)
        self.assertEqual(len(self.w.of("llm")), 1, "no extra round needed for a structured call")

    def test_tools_have_docstrings_for_the_llm(self):
        for tool in self.tools.for_llm():
            self.assertTrue(tool.__doc__ and "Args:" in tool.__doc__, tool.__name__)

    def test_system_prompt_mentions_every_tool(self):
        prompt = fakes.LargeLanguageModel.last.system_prompt
        for name in ("web_search", "get_weather", "show_emotion"):
            self.assertIn(name, prompt)

    def test_tool_calls_written_as_text_are_ignored_when_tools_are_off(self):
        self.settings.use_tools = False
        self.say("Do you like me?", "")
        self.answer_with('show_emotion("heart")\nYes!')
        self.wake()
        self.run_until_idle()
        self.assertEqual(self.w.bridge("show_emotion"), [])
        self.assertEqual(self.w.spoken(), ["Yes!"])

    def test_web_search_combines_duckduckgo_and_wikipedia(self):
        self.w.http_routes = {
            "https://api.duckduckgo.com/": {"Answer": "", "AbstractText": "DDG says hi.", "RelatedTopics": [{"Text": "Topic A"}]},
            "https://en.wikipedia.org/w/api.php": {"query": {"search": [{"title": "Arduino"}]}},
            "https://en.wikipedia.org/api/rest_v1/page/summary/": {"extract": "Arduino is an open-source platform."},
        }
        result = self.tools.web_search("Arduino")
        self.assertIn("DDG says hi.", result)
        self.assertIn("Topic A", result)
        self.assertIn("Arduino: Arduino is an open-source platform.", result)

    def test_web_search_result_is_capped_for_the_local_model(self):
        self.w.http_routes = {"https://api.duckduckgo.com/": {"AbstractText": "x" * 10000}}
        self.assertLessEqual(len(self.tools.web_search("long")), 2000)

    def test_web_search_survives_network_errors(self):
        self.w.http_routes = {"https://": ConnectionError("offline")}
        self.assertEqual(self.tools.web_search("anything"), "No results found.")

    def test_get_weather_uses_builtin_brick_with_clamped_days(self):
        result = self.tools.get_weather("Berlin", days_ahead=10)
        city, kwargs = self.w.of("weather")[0][1:]
        self.assertEqual(city, "Berlin")
        self.assertEqual(kwargs["forecast_days"], 7)
        self.assertEqual(kwargs["timezone"], str(self.settings.time_zone))
        self.assertIn("Slight rain", result)

    def test_show_emotion_draws_symbol_and_plays_jingle(self):
        result = self.tools.show_emotion("Heart")
        self.assertIn(("bridge", "show_emotion", 0), self.w.events)
        self.assertEqual([e[1] for e in self.w.of("tone")], [n for n, _ in EARCONS["heart"]])
        self.assertTrue(self.expression.emotion_shown)
        self.assertIn("heart", result)

    def test_show_emotion_rejects_unknown_symbols(self):
        result = self.tools.show_emotion("dancing")
        self.assertEqual(self.w.bridge("show_emotion"), [])
        self.assertIn("Unknown emotion", result)

    def test_every_emotion_has_a_jingle(self):
        for name in EMOTIONS:
            self.assertIn(name, EARCONS, name)

    def test_emotion_stays_visible_after_speaking(self):
        def loving(prompt, tools):
            yield 'show_emotion("heart")\n'
            yield "Yes, I do!"

        self.w.llm_responder = loving
        self.say("Do you like me?", "")
        self.wake()
        self.run_until_idle()
        self.assertIn(("bridge", "show_emotion", 0), self.w.events)
        self.assertIn("Yes, I do!", self.w.spoken())
        self.assertIn(mock.call(self.settings.emotion_hold_seconds), self.sleep.call_args_list)


class TestToolCallsWrittenAsText(AssistantTestCase):
    """The model on the board writes tool calls into its answer. Cases copied from the board log."""

    def converse(self, question, first_answer, second_answer="Sure."):
        prompts = []

        def responder(prompt, tools):
            prompts.append(prompt)
            yield from (second_answer if prompt.startswith("[Results") else first_answer)

        self.w.llm_responder = responder
        self.say(question, "")
        self.wake()
        self.run_until_idle()
        return prompts

    def assertNothingTechnicalSpoken(self):
        for text in self.w.spoken():
            for bad in ("show_emotion", "get_weather", "web_search", "tool_call", "{", "}", "*", "\U0001F60A"):
                self.assertNotIn(bad, text.lower(), text)

    def test_joke_with_emotion_on_the_last_line(self):
        self.converse("Tell me a joke.", [
            "Why did the tomato turn red?  \n",
            'Because it saw its salad dressing and thought, "I’m in love!"  \n',
            "I’m *so* glad \U0001F60A you asked — I’ve never seen a tomato so smitten! \U0001F60A  \n",
            'show_emotion("heart")',
        ])
        self.assertIn(("bridge", "show_emotion", EMOTIONS.index("heart")), self.w.events)
        self.assertNothingTechnicalSpoken()
        self.assertEqual(self.w.spoken()[0], "Why did the tomato turn red?")
        self.assertIn("so glad you asked", self.w.spoken()[2])

    def test_emotion_written_without_quotes_and_capitalized(self):
        self.converse("Why is the sky blue?", [
            "The sky appears blue because blue light scatters more. ",
            "I'm pretty sure that's why we see it like that. Show_emotion(heart)",
        ])
        self.assertIn(("bridge", "show_emotion", EMOTIONS.index("heart")), self.w.events)
        self.assertNothingTechnicalSpoken()
        self.assertEqual(len(self.w.spoken()), 2)

    def test_weather_requested_as_json_tool_call(self):
        prompts = self.converse("What's the weather today?", [
            "Ah, you mean the weather today? Well, let me check what’s forecasted for now.  \n",
            '{"tool_calls": [{"type": "function", "function": {"name": "get_weather", "arguments": {"city": "Berlin", "days_ahead": 0}}}]}  \n',
            '{"tool_calls": []}  \n',
            'show_emotion("happy")',
        ], second_answer="It's going to be a bit rainy in Berlin today.")
        city, kwargs = self.w.of("weather")[0][1:]
        self.assertEqual((city, kwargs["forecast_days"]), ("Berlin", 1))
        self.assertIn("Weather in Berlin: Slight rain", prompts[1])
        self.assertEqual(self.w.spoken(), [
            "Ah, you mean the weather today?",
            "Well, let me check what’s forecasted for now.",
            "It's going to be a bit rainy in Berlin today.",
        ])
        self.assertNothingTechnicalSpoken()

    def test_qwen_tool_call_tags(self):
        self.w.http_routes = {"https://api.duckduckgo.com/": {"AbstractText": "Mount Everest is 8849 m high."}}
        prompts = self.converse("How high is Mount Everest?", [
            "<tool_call>\n", '{"name": "web_search", "arguments": {"query": "Mt. Everest height"}}', "\n</tool_call>",
        ], second_answer="About eight thousand eight hundred meters.")
        self.assertIn("8849", prompts[1])
        self.assertIn(self.w.spoken()[0], self.lang.search_fillers)  # nothing said yet, so a filler
        self.assertEqual(self.w.spoken()[1], "About eight thousand eight hundred meters.")

    def test_python_style_call_with_keywords(self):
        self.converse("Weather in New York tomorrow?", ['get_weather(city="New York", days_ahead=1)'])
        city, kwargs = self.w.of("weather")[0][1:]
        self.assertEqual((city, kwargs["forecast_days"]), ("New York", 2))

    def test_text_after_a_lookup_is_not_spoken(self):
        self.converse("Who won?", ['web_search("who won the match")\n', "Team A won three to one. "],
                      second_answer="I couldn't find it.")
        self.assertNotIn("Team A won three to one.", self.w.spoken())

    def test_gives_up_after_too_many_lookups(self):
        self.w.http_routes = {"https://": ConnectionError("offline")}
        self.converse("Something obscure?", ['web_search("thing")'], second_answer='web_search("thing again")')
        self.assertEqual(len(self.w.of("llm")), self.settings.max_lookup_rounds + 1)
        self.assertEqual(self.w.spoken()[-1], self.lang.couldnt_find)

    def test_splitter_keeps_tool_calls_in_one_piece(self):
        pieces, rest = textcalls.split_speakable('First. web_search("Mt. Everest") Second! Third')
        self.assertEqual(pieces, ["First.", ' web_search("Mt. Everest") Second!'])
        self.assertEqual(rest, " Third")


class TestIdleFace(AssistantTestCase):
    def idle_modes(self):
        return [e[2] for e in self.w.bridge("set_idle_mode")]

    def test_starts_awake(self):
        modes = [e[2] for e in self.startup_events if e[:2] == ("bridge", "set_idle_mode")]
        self.assertEqual(modes, [face_module.IDLE_AWAKE])

    def test_falls_asleep_after_inactivity(self):
        self.c.last_activity -= self.settings.sleep_after_seconds - 10
        self.c.step()
        self.assertEqual(self.idle_modes(), [], "fell asleep too early")
        self.c.last_activity -= 20
        self.c.step()
        self.assertEqual(self.idle_modes(), [face_module.IDLE_SLEEPING])
        self.c.step()
        self.assertEqual(self.idle_modes(), [face_module.IDLE_SLEEPING], "mode should only be sent when it changes")

    def test_default_sleep_delay_is_five_minutes(self):
        self.assertEqual(config.Settings().sleep_after_seconds, 300)

    def test_wakes_up_after_a_conversation(self):
        self.c.last_activity -= self.settings.sleep_after_seconds + 1
        self.c.step()
        self.say("Hello there", "")
        self.wake()
        self.run_until_idle()
        self.assertEqual(self.idle_modes(), [face_module.IDLE_SLEEPING, face_module.IDLE_AWAKE])

    def test_off_and_awake_settings(self):
        self.settings.idle_face = "off"
        self.c.update_idle_face()
        self.settings.idle_face = "awake"
        self.c.last_activity -= 10_000
        self.c.update_idle_face()
        self.assertEqual(self.idle_modes(), [face_module.IDLE_OFF, face_module.IDLE_AWAKE])


class TestSoundEffects(AssistantTestCase):
    def test_sounds_can_be_switched_off(self):
        fakes.reset()
        c = setup_module.build_assistant(config.Settings(sound_effects=False))
        c.sounds.play("wake")
        c.brain.tools.show_emotion("heart")
        self.assertEqual(fakes.world.of("tone"), [])

    def test_broken_sound_never_breaks_the_conversation(self):
        self.c.sounds.generator.play_tone = mock.Mock(side_effect=OSError("speaker busy"))
        self.say("Tell me something", "")
        self.answer_with("Here you go.")
        self.wake()
        self.run_until_idle()
        self.assertIn("Here you go.", self.w.spoken())

    def test_all_earcon_notes_are_valid(self):
        note = re.compile(r"^(REST|[A-G](#|B)?[0-7])$")
        for name, notes in EARCONS.items():
            for n, seconds in notes:
                self.assertRegex(n, note, name)
                self.assertGreater(seconds, 0)
                self.assertLessEqual(seconds, 0.5, f"{name} is too long for a chime")


# ---------------------------------------------------------------------------
class TestGerman(AssistantTestCase):
    language = "de"

    def test_everything_is_german(self):
        self.assertEqual(self.c.ears.asr.language, "de")
        self.assertIn("Antworte immer auf Deutsch", fakes.LargeLanguageModel.last.system_prompt)
        self.assertIn("web_search", fakes.LargeLanguageModel.last.system_prompt)

    def test_prompt_has_german_date(self):
        self.set_time(17)
        self.say("Wie spät ist es?", "")
        self.wake()
        self.run_until_idle()
        prompt = self.w.of("llm")[0][1]
        self.assertIn("Mittwoch, der 7. Oktober 2026, 17:30 Uhr", prompt)
        self.assertIn("(Nachmittag)", prompt)
        self.assertIn("Nutzer: Wie spät ist es?", prompt)

    def test_quick_replies_and_farewell(self):
        self.set_time(9)
        self.say("Guten Morgen!", "Danke schön!", "Tschüss!")
        self.wake()
        self.run_until_idle()
        spoken = self.w.spoken()
        self.assertIn(spoken[0], self.lang.greetings["morning"])
        self.assertIn(spoken[1], self.lang.thanks_replies)
        self.assertIn(spoken[2], self.lang.farewells["morning"])
        self.assertEqual(self.w.of("llm"), [])

    def test_understands_german_phrases(self):
        lang = self.lang
        for text in ["Tschüss!", "Das war's.", "Okay, tschüss", "Vergiss es."]:
            self.assertTrue(lang.is_exit(text), text)
        for text in ["Danke!", "Vielen Dank.", "Danke dir", "Okay, danke."]:
            self.assertTrue(lang.is_thanks(text), text)
        for text in ["Hallo!", "Guten Abend.", "Servus", "Moin"]:
            self.assertTrue(lang.is_greeting(text), text)
        for text in ["Wo ist die Bushaltestelle?", "Danke für die Info über Rom", "Hallo, wie wird das Wetter?"]:
            self.assertFalse(lang.is_exit(text) or lang.is_thanks(text) or lang.is_greeting(text), text)

    def test_german_fillers_and_search(self):
        self.w.http_routes = {"https://de.wikipedia.org/w/api.php": {"query": {"search": [{"title": "Zugspitze"}]}},
                              "https://de.wikipedia.org/api/rest_v1/page/summary/": {"extract": "2962 Meter hoch."},
                              "https://api.duckduckgo.com/": {}}

        def responder(prompt, tools):
            if prompt.startswith("[Ergebnisse"):
                assert "2962" in prompt
                yield "Die Zugspitze ist knapp dreitausend Meter hoch."
            else:
                yield 'web_search("Zugspitze Höhe")'

        self.w.llm_responder = responder
        self.say("Wie hoch ist die Zugspitze?", "")
        self.wake()
        self.run_until_idle()
        spoken = self.w.spoken()
        self.assertIn(spoken[0], self.lang.search_fillers)
        self.assertEqual(spoken[1], "Die Zugspitze ist knapp dreitausend Meter hoch.")

    def test_didnt_catch_in_german(self):
        self.say("")
        self.wake()
        self.run_until_idle()
        self.assertIn(self.w.spoken()[0], self.lang.didnt_catch)


class TestLanguages(unittest.TestCase):
    CODES = ("en", "de")

    def test_unknown_language_is_rejected(self):
        with self.assertRaises(ValueError):
            load_language("fr")

    def test_every_language_is_complete(self):
        parts = {"morning", "afternoon", "evening", "night"}
        for code in self.CODES:
            with self.subTest(language=code):
                lang = load_language(code)
                self.assertEqual(lang.code, code)
                self.assertEqual(set(lang.greetings), parts)
                self.assertEqual(set(lang.farewells), parts)
                self.assertEqual(set(lang.part_of_day_names), parts)
                for name in ("thinking_fillers", "search_fillers", "weather_fillers", "didnt_catch",
                             "thanks_replies", "error_replies"):
                    self.assertTrue(getattr(lang, name), name)
                for options in list(lang.greetings.values()) + list(lang.farewells.values()):
                    self.assertTrue(options)
                self.assertEqual(len(lang.weekdays), 7)
                self.assertEqual(len(lang.months), 12)
                for field in ("{now}", "{part_of_day}", "{text}"):
                    self.assertIn(field, lang.user_prompt)
                self.assertIn("{results}", lang.lookup_results_prompt)
                for tool in ("web_search", "get_weather", "show_emotion"):
                    self.assertIn(tool, lang.tools_prompt)
                for emotion in EMOTIONS:
                    self.assertIn(emotion, lang.tools_prompt)
                self.assertTrue(lang.lookup_results_prompt.startswith("[") and lang.tts_model)

    def test_said_phrases_are_not_recognized_as_commands(self):
        # e.g. the farewell must not be mistaken for a greeting, a filler not for an exit
        for code in self.CODES:
            lang = load_language(code)
            for text in lang.thinking_fillers + lang.search_fillers + lang.weather_fillers:
                self.assertFalse(lang.is_exit(text), (code, text))

    def test_tts_model_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            app_yaml = Path(tmp) / "app.yaml"
            app_yaml.write_text("bricks:\n- arduino:tts:\n    model: piper-tts-de  # comment\n")
            self.assertTrue(setup_module.check_tts_model(load_language("de"), app_yaml))
            self.assertFalse(setup_module.check_tts_model(load_language("en"), app_yaml))
            app_yaml.write_text("bricks:\n- arduino:tts: {}\n")  # brick default is English
            self.assertTrue(setup_module.check_tts_model(load_language("en"), app_yaml))
            self.assertFalse(setup_module.check_tts_model(load_language("de"), app_yaml))

    def test_repository_app_yaml_matches_config_language(self):
        language = load_language(config.LANGUAGE)
        self.assertTrue(setup_module.check_tts_model(language, APP_YAML))


class TestProjectConsistency(unittest.TestCase):
    """Static checks across app.yaml, python/ and sketch.ino."""

    @classmethod
    def setUpClass(cls):
        cls.python = "\n".join(p.read_text() for p in PYTHON.rglob("*.py"))
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
        used = set(re.findall(r"from arduino\.app_bricks\.(\w+) import", self.python))
        declared = set(re.findall(r"^- arduino:(\w+):", self.app, re.M))
        self.assertEqual(used - declared, set())

    def test_python_only_calls_rpcs_the_sketch_provides(self):
        called = set(re.findall(r'Bridge\.call\("(\w+)"', self.python))
        provided = set(re.findall(r'Bridge\.provide\("(\w+)"', self.sketch))
        self.assertTrue(called)
        self.assertEqual(called - provided, set())

    def test_emotions_match_sketch_bitmaps(self):
        names = re.findall(r"\{ // (\d+): (\w+)", self.sketch)
        self.assertEqual([n for _, n in names], EMOTIONS)
        self.assertEqual(int(re.search(r"NUM_EMOTIONS = (\d+)", self.sketch).group(1)), len(EMOTIONS))

    def test_license_and_credit(self):
        self.assertIn("GNU GENERAL PUBLIC LICENSE", (ROOT / "LICENSE").read_text())
        self.assertIn("mcmchris/keyword-asr-local-llm-kokoro-tts", (ROOT / "README.md").read_text())


if __name__ == "__main__":
    unittest.main()


class TestEntryPoint(unittest.TestCase):
    def test_main_py_runs_like_on_the_board(self):
        """App Lab runs `python /app/python/main.py` from /app: check the imports work that way."""
        import os
        import subprocess

        with tempfile.TemporaryDirectory() as tmp:
            # Installs the fake bricks before main.py runs (the fake App.run returns at once)
            Path(tmp, "sitecustomize.py").write_text(
                f"import sys\nsys.path.insert(0, {str(TESTS)!r})\nimport fakes\nfakes.install()\n")
            env = dict(os.environ, PYTHONPATH=tmp)
            result = subprocess.run([sys.executable, str(PYTHON / "main.py")], cwd=ROOT, env=env,
                                    capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Ready!", result.stdout)
