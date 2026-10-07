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
    def test_main_creates_one_microphone(self):
        self.assertEqual(MAIN.read_text().count("Microphone("), 1)

    def test_wake_word_and_speech_recognition_use_separate_streams(self):
        # One Microphone object must never be read by two bricks at once:
        # every read takes the chunk away from the other reader.
        kws_mic = fakes.KeywordSpotting.instances[0].mic
        self.assertIsNot(kws_mic, self.m.asr.mic)
        self.assertIs(self.m.asr.mic, self.m.mic)

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
        self.assertTrue(all(mic_on for _, _, mic_on in listens))

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
        self.m.THINKING_FILLER_SECONDS = 0.01

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
        self.m.voice.stop_talking()
        self.assertIn(("tts_cancel",), self.w.events)
        self.assertFalse(self.m.voice.is_busy())


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
        self.assertEqual(self.w.spoken()[:3], ["I'm great.", "Thanks for asking!", "What about you?"])

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
        self.assertIn(spoken[0], self.m.SEARCH_FILLERS)
        self.assertTrue(spoken[1].startswith("It is about"))
        self.assertFalse(any(t in self.m.THINKING_FILLERS for t in spoken), "thinking filler on top of search filler")

    def test_weather_filler(self):
        self.m.run_lookups([("get_weather", {"city": "Berlin"})])
        self.m.voice.wait()
        self.assertIn(self.w.spoken()[0], self.m.WEATHER_FILLERS)


class TestTools(AssistantTestCase):
    def test_tools_are_registered_with_the_llm(self):
        tools = fakes.LargeLanguageModel.last.tools
        self.assertEqual(set(tools), {"web_search", "get_weather", "show_emotion"})
        self.assertIs(tools["web_search"], self.m.web_search)

    def test_no_tools_registered_when_switched_off(self):
        main = MAIN.read_text()
        self.assertIn("tools=TOOLS if USE_TOOLS else None", main)

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
        self.assertIn(spoken[0], self.m.SEARCH_FILLERS)
        self.assertEqual(spoken[1], "Wow, it's about eight thousand eight hundred meters high!")
        self.assertIn(("bridge", "show_emotion", self.m.EMOTIONS.index("surprised")), self.w.events)
        self.assertEqual(len(self.w.of("llm")), 1, "no extra round needed for a structured call")

    def test_tools_have_docstrings_for_the_llm(self):
        for tool in (self.m.web_search, self.m.get_weather, self.m.show_emotion):
            self.assertTrue(tool.__doc__ and "Args:" in tool.__doc__, tool.__name__)

    def test_system_prompt_mentions_every_tool(self):
        for name in ("web_search", "get_weather", "show_emotion"):
            self.assertIn(name, self.m.SYSTEM_PROMPT)

    def test_actions_can_be_switched_off(self):
        self.m.USE_TOOLS = False
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
            yield 'show_emotion("heart")\n'
            yield "Yes, I do!"

        self.w.llm_responder = loving
        self.say("Do you like me?", "")
        self.wake()
        self.run_until_idle()
        self.assertIn(("bridge", "show_emotion", 0), self.w.events)
        self.assertIn("Yes, I do!", self.w.spoken())
        self.assertIn(mock.call(self.m.EMOTION_HOLD_SECONDS), self.sleep.call_args_list)


class TestActionsWrittenAsText(AssistantTestCase):
    """The model on the board writes actions into its answer. Cases copied from the board log."""

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
            for bad in ("show_emotion", "get_weather", "web_search", "tool_call", "{", "}", "*", "😊"):
                self.assertNotIn(bad, text.lower(), text)

    def test_joke_with_emotion_on_the_last_line(self):
        self.converse("Tell me a joke.", [
            "Why did the tomato turn red?  \n",
            'Because it saw its salad dressing and thought, "I\u2019m in love!"  \n',
            "I\u2019m *so* glad \U0001F60A you asked \u2014 I\u2019ve never seen a tomato so smitten! \U0001F60A  \n",
            'show_emotion("heart")',
        ])
        self.assertIn(("bridge", "show_emotion", self.m.EMOTIONS.index("heart")), self.w.events)
        self.assertNothingTechnicalSpoken()
        self.assertEqual(self.w.spoken()[0], "Why did the tomato turn red?")
        self.assertIn("so glad you asked", self.w.spoken()[2])

    def test_emotion_written_without_quotes_and_capitalized(self):
        self.converse("Why is the sky blue?", [
            "The sky appears blue because blue light scatters more. ",
            "I'm pretty sure that's why we see it like that. Show_emotion(heart)",
        ])
        self.assertIn(("bridge", "show_emotion", self.m.EMOTIONS.index("heart")), self.w.events)
        self.assertNothingTechnicalSpoken()
        self.assertEqual(len(self.w.spoken()), 2)

    def test_weather_requested_as_json_tool_call(self):
        prompts = self.converse("What's the weather today?", [
            "Ah, you mean the weather today? Well, let me check what\u2019s forecasted for now.  \n",
            '{"tool_calls": [{"type": "function", "function": {"name": "get_weather", "arguments": {"city": "Berlin", "days_ahead": 0}}}]}  \n',
            '{"tool_calls": []}  \n',
            'show_emotion("happy")',
        ], second_answer="It's going to be a bit rainy in Berlin today.")
        city, kwargs = self.w.of("weather")[0][1:]
        self.assertEqual((city, kwargs["forecast_days"]), ("Berlin", 1))
        self.assertIn("Weather in Berlin: Slight rain", prompts[1])
        self.assertEqual(self.w.spoken(), [
            "Ah, you mean the weather today?",
            "Well, let me check what\u2019s forecasted for now.",
            "It's going to be a bit rainy in Berlin today.",
        ])
        self.assertNothingTechnicalSpoken()

    def test_qwen_tool_call_tags(self):
        self.w.http_routes = {"https://api.duckduckgo.com/": {"AbstractText": "Mount Everest is 8849 m high."}}
        prompts = self.converse("How high is Mount Everest?", [
            "<tool_call>\n", '{"name": "web_search", "arguments": {"query": "Mt. Everest height"}}', "\n</tool_call>",
        ], second_answer="About eight thousand eight hundred meters.")
        self.assertIn("8849", prompts[1])
        self.assertIn(self.w.spoken()[0], self.m.SEARCH_FILLERS)  # nothing said yet, so a filler
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
        self.assertEqual(len(self.w.of("llm")), self.m.MAX_LOOKUP_ROUNDS + 1)
        self.assertEqual(self.w.spoken()[-1], "Sorry, I couldn't find that out right now.")

    def test_splitter_keeps_actions_in_one_piece(self):
        pieces, rest = self.m.split_speakable('First. web_search("Mt. Everest") Second! Third')
        self.assertEqual(pieces, ["First.", ' web_search("Mt. Everest") Second!'])
        self.assertEqual(rest, " Third")

    def test_speech_recognition_language_is_fixed(self):
        self.assertEqual(self.m.asr.language, self.m.ASR_LANGUAGE)
        self.assertEqual(self.m.ASR_LANGUAGE, "en")
        self.assertIn("Always answer in English", self.m.SYSTEM_PROMPT)


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
