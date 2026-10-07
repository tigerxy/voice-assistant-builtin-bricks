# Offline Voice Assistant — built-in bricks only (Arduino VENTUNO Q)

A rework of [mcmchris/keyword-asr-local-llm-kokoro-tts](https://github.com/mcmchris/keyword-asr-local-llm-kokoro-tts)
that drops the custom Kokoro brick and uses only Arduino App Lab's built-in bricks.

## Bricks

| Stage | Brick | Python class |
|---|---|---|
| Wake word ("Hey Arduino") | `arduino:keyword_spotting` | `KeywordSpotting` |
| Speech-to-text | `arduino:asr` | `AutomaticSpeechRecognition` |
| Local LLM (NPU) | `arduino:llm` | `LargeLanguageModel` |
| Text-to-speech | `arduino:tts` | `TextToSpeech` |
| Weather (LLM tool) | `arduino:weather_forecast` | `WeatherForecast` |
| Sound effects | `arduino:sound_generator` | `SoundGenerator` |

No `bricks/` folder, no custom code to maintain.

## Language: English or German

The assistant speaks and understands **English** or **German**. To switch:

1. In `python/config.py` set `LANGUAGE = "de"` (or `"en"`).
2. In `app.yaml` set the matching voice:
   ```yaml
   - arduino:tts:
       model: piper-tts-de   # English: piper-tts-en
   ```

That switches speech recognition, the LLM's instructions, every phrase the
assistant says, the words it understands ("Tschüss", "Danke"), the date it is
told and the Wikipedia edition for web search. The wake word stays "Hey Arduino"
(the built-in model only knows that one). If `LANGUAGE` and the voice in
`app.yaml` don't match, the app prints a warning at start-up.

All texts live in `python/languages/en.py` and `python/languages/de.py`. To add
another language, copy one of them, translate it and register it in
`python/languages/__init__.py`.

## Conversations

Say **"Hey Arduino"** once, then talk normally:

1. After each answer the assistant keeps listening for a follow-up
   (`follow_up_seconds`, default 6 s). No wake word needed.
2. The LLM keeps the conversation history (`memory_messages`), so follow-ups like
   "and tomorrow?" or "how old is he?" work.
3. The conversation ends when you stay silent, or say "stop", "bye", "goodbye",
   "that's all" or "never mind". The history is then cleared, so the next
   "Hey Arduino" starts fresh.

The speech recognition microphone only opens after the assistant has finished
speaking, so it never hears itself. The wake word detector reads its own stream
of the same microphone (ALSA shared mode) and keeps running the whole time.
The trade-off: you can't interrupt it mid-answer.

## Human touches

- **Sound effects**: a rising chime when it wakes up, a falling one when the
  conversation ends, a short jingle with every emotion symbol and a buzz on errors
  (`sound_generator` brick, shares the speaker with TTS). Turn off with `sound_effects = False`.
- **Filler words**: "Let me look that up." before a web search, "Let me check the
  weather." before a forecast, and "Hmm, let me think." when the first words take
  longer than `thinking_filler_seconds`.
- **Natural reactions**: instant replies to "thanks" and "hello" (time-aware:
  "Good morning!", "You're up late!") without waiting for the LLM, varied
  farewells ("Good night, sleep well!"), "Sorry, I didn't catch that" when it
  wakes but hears nothing, and a spoken apology instead of silence on errors.
  The system prompt also asks for varied, warm phrasing.
- **Idle life**: while waiting, the LED matrix shows dim eyes that blink and look
  around. After 5 minutes without a conversation (`sleep_after_seconds`) it falls
  asleep: closed eyes with a floating "z". "Hey Arduino" wakes it up again.
  Set `idle_face` to `"awake"` or `"off"` to change that.

All settings are in `python/config.py`, all texts in `python/languages/`, the
sounds in `python/assistant/sounds.py`.

## Tools: web search, weather and emotions

The local LLM can use three tools when it decides it needs them:

- **`web_search("query")`**: looks things up via the DuckDuckGo Instant Answer API
  and Wikipedia (no API key). There is no web search brick in Arduino's library,
  so this is a small Python function. It's good for facts and background
  knowledge, not for breaking news.
- **`get_weather("City", days_ahead)`**: uses the built-in `weather_forecast` brick
  (open-meteo.com, no API key). It returns a description like "Slight rain",
  not temperatures.
- **`show_emotion("heart")`**: shows a pulsing symbol on the LED matrix while the
  assistant answers: `heart`, `happy`, `sad`, `surprised`, `wink`, `angry`,
  `confused` (question mark) or `star`. Ask "Do you like me?" and it answers
  "Yes, I do!" with a heart. The symbol stays until the assistant listens again.
  To add a symbol, append a 13×8 bitmap to `EMOTION_BITMAPS` in `sketch.ino`
  and its name to `EMOTIONS` in `python/assistant/face.py` (same position in both lists).

**How it works:** the three functions are registered with the LLM brick
(`LargeLanguageModel(system_prompt=..., tools=tools.for_llm())`). When the model returns a
real tool call, the brick runs the function and the model continues its answer
with the result. The small model on the VENTUNO Q often writes the call into its
answer as text instead (`show_emotion("heart")`, `Show_emotion(heart)`, a
`{"tool_calls": [...]}` JSON block or `<tool_call>` tags), which the brick can't
run. So the assistant also finds those in the streamed answer
(`assistant/textcalls.py`), carries them out and never reads them aloud; for a
lookup written as text, the result is sent back to the model in a second round (at most `max_lookup_rounds` per question).

Web search and weather need internet access on the board. Set
`use_tools = False` in `config.py` to turn all tools off.

## What changed vs. the Kokoro version

- **TTS**: `TextToSpeech` from `arduino.app_bricks.tts` replaces `KokoroTTS`.
  It plays directly through a `Speaker` peripheral, so the temporary `.wav`
  file and the `aplay` shell call are gone.
- **Lower latency**: the LLM reply is streamed and each finished sentence goes
  into a small speech queue (`assistant/voice.py`), so the assistant starts
  talking before the LLM has finished generating. The queue only uses
  `TextToSpeech.speak(text)`, so it works with older brick libraries too
  (0.11/0.12 have no background speech of their own).
- **Real conversations** with follow-up questions, plus web search and weather tools (see above).
- **Clean errors**: speech is cancelled and the assistant apologizes instead of going silent.
- **English or German**, switchable in `config.py` (see above).
- **Wake word** is now "Hey Arduino": the built-in keyword spotting model
  only knows that phrase (the original used a custom Edge Impulse model for "Ventuno").
- The MCU sketch keeps the original animations and adds emotion symbols (`show_emotion` RPC) and an idle face (`set_idle_mode` RPC).

## Voice

The voices are the built-in Piper models (`piper-tts-en`, `piper-tts-de`, set in
`app.yaml`). Speech recognition is fixed to the configured language: with
automatic detection, Whisper sometimes heard English as German ("Why is the sky
blue?" became "Wo ist das Skyblue?"). Built-in voices sound different from
Kokoro; that is the trade-off for staying on the stock bricks.

## Hardware

- VENTUNO Q (Network or SBC mode)
- USB microphone
- USB speaker / headset (a powered USB-C hub is recommended)

By default TTS uses the first plugged speaker. To force a specific ALSA device,
see the commented `Speaker(...)` line in `python/assistant/setup.py`.

## Tests

The tests run on any computer with Python 3.10+, no board and no extra packages needed:

```bash
python3 -m unittest discover -s tests -b -v
```

- `tests/test_assistant.py` runs the assistant against fake bricks
  (`tests/fakes.py`, same API as the brick library on the board) and checks the
  conversation flow, follow-ups, exit phrases, quick replies, filler words, tools
  (web search, weather, emotions, also when written as text), sound effects, the
  idle face timer, both languages, and that `app.yaml`, `python/` and
  `sketch.ino` agree with each other. It also starts `main.py` the way App Lab
  does.
- `tests/test_sketch.py` compiles the real `sketch.ino` for your computer with small
  stand-ins for the Arduino libraries (`tests/sketch_host/`) and checks the LED
  frames: idle eyes, blinking, sleeping face, scanner, waves and all emotion
  symbols. It needs `g++` or `clang++` and is skipped otherwise.

They can't replace a test on the real VENTUNO Q: audio, the AI models and the
LED hardware are simulated.

## Project layout

```
app.yaml                    bricks, TTS voice
python/
  main.py                   entry point: builds the assistant, runs the App loop
  config.py                 all settings, including LANGUAGE
  languages/                everything the assistant says and understands
    __init__.py             the Language structure
    en.py, de.py            English and German
  assistant/
    setup.py                creates all parts and connects them
    conversation.py         the conversation flow (state machine)
    ears.py                 microphone, wake word, speech recognition
    brain.py                the LLM: answers, lookups
    tools.py                web search, weather, emotions (LLM tools)
    textcalls.py            tool calls written as text, cleaning text for speech
    expression.py           speech + face + sounds as one personality
    voice.py                speech queue on top of the TTS brick
    face.py                 the LED matrix (talks to sketch.ino)
    sounds.py               chimes and jingles
    timeofday.py            morning / afternoon / evening / night
sketch/sketch.ino           LED matrix animations on the MCU
tests/                      unit tests, see "Tests"
```

## License

This project is licensed under the [GNU General Public License v3.0](LICENSE).

It is derived from [keyword-asr-local-llm-kokoro-tts](https://github.com/mcmchris/keyword-asr-local-llm-kokoro-tts)
by [mcmchris](https://github.com/mcmchris), which is also licensed under GPL-3.0.
The MCU sketch (`sketch/sketch.ino`) and the overall application structure come from
that project; the Python backend was adapted to use only Arduino's built-in bricks.
