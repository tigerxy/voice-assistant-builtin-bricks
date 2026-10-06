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

No `bricks/` folder, no custom code to maintain.

## Conversations

Say **"Hey Arduino"** once, then talk normally:

1. After each answer the assistant keeps listening for a follow-up
   (`FOLLOW_UP_SECONDS`, default 6 s). No wake word needed.
2. The LLM keeps the conversation history (`MEMORY_MESSAGES`), so follow-ups like
   "and tomorrow?" or "how old is he?" work.
3. The conversation ends when you stay silent, or say "stop", "bye", "goodbye",
   "that's all" or "never mind". The history is then cleared, so the next
   "Hey Arduino" starts fresh.

The microphone only opens after the assistant has finished speaking, so it
never hears itself. The trade-off: you can't interrupt it mid-answer.

## Tools: web search, weather and emotions

The local LLM can call three tools when it decides it needs them:

- **`web_search(query)`**: looks things up via the DuckDuckGo Instant Answer API
  and Wikipedia (no API key). There is no web search brick in Arduino's library,
  so this is a small Python function passed to the LLM brick's `tools=` argument.
  It's good for facts and background knowledge, not for breaking news.
- **`get_weather(city, days_ahead)`**: uses the built-in `weather_forecast` brick
  (open-meteo.com, no API key). It returns a description like "Slight rain",
  not temperatures.

- **`show_emotion(emotion)`**: shows a pulsing symbol on the LED matrix while the
  assistant answers: `heart`, `happy`, `sad`, `surprised`, `wink`, `angry`,
  `confused` (question mark) or `star`. Ask "Do you like me?" and it answers
  "Yes, I do!" with a heart. The symbol stays until the assistant listens again.
  To add a symbol, append a 13×8 bitmap to `EMOTION_BITMAPS` in `sketch.ino`
  and its name to `EMOTIONS` in `main.py` (same position in both lists).

Web search and weather need internet access on the board. If your LLM runner doesn't support tool
calling, set `USE_TOOLS = False` in `main.py` and the assistant works offline
without them.

## What changed vs. the Kokoro version

- **TTS**: `TextToSpeech` from `arduino.app_bricks.tts` replaces `KokoroTTS`.
  It plays directly through a `Speaker` peripheral, so the temporary `.wav`
  file and the `aplay` shell call are gone.
- **Lower latency**: the LLM reply is streamed and each finished sentence is
  queued with `tts.speak(sentence, block=False)`, so the assistant starts
  talking before the LLM has finished generating.
- **Real conversations** with follow-up questions, plus web search and weather tools (see above).
- **Clean shutdown**: `tts.cancel()` on errors, `tts.stop()` on exit.
- **Time zone** is a single constant (`TIME_ZONE`) at the top of `main.py`.
- **Wake word** is now "Hey Arduino": the built-in keyword spotting model
  only knows that phrase (the original used a custom Edge Impulse model for "Ventuno").
- The MCU sketch keeps the original animations and adds emotion symbols (`show_emotion` RPC).

## Voice / language

The default model is English Piper. To change it, override the model in `app.yaml`:

```yaml
- arduino:tts:
    model: melo-tts-en   # also: piper-tts-de, piper-tts-it, melo-tts-es, melo-tts-zh, ...
```

Built-in voices will sound different from Kokoro; that is the trade-off for
staying on the stock bricks.

## Hardware

- VENTUNO Q (Network or SBC mode)
- USB microphone
- USB speaker / headset (a powered USB-C hub is recommended)

By default TTS uses the first plugged speaker. To force a specific ALSA device,
see the commented `Speaker(...)` line in `python/main.py`.

## Project layout

```
app.yaml
python/main.py
sketch/sketch.ino
sketch/sketch.yaml
```

## License

This project is licensed under the [GNU General Public License v3.0](LICENSE).

It is derived from [keyword-asr-local-llm-kokoro-tts](https://github.com/mcmchris/keyword-asr-local-llm-kokoro-tts)
by [mcmchris](https://github.com/mcmchris), which is also licensed under GPL-3.0.
The MCU sketch (`sketch/sketch.ino`) and the overall application structure come from
that project; the Python backend was adapted to use only Arduino's built-in bricks.
