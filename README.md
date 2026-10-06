# Offline Voice Assistant — built-in bricks only (Arduino VENTUNO Q)

A rework of [mcmchris/keyword-asr-local-llm-kokoro-tts](https://github.com/mcmchris/keyword-asr-local-llm-kokoro-tts)
that drops the custom Kokoro brick and uses only Arduino App Lab's built-in bricks.

## Bricks

| Stage | Brick | Python class |
|---|---|---|
| Wake word ("Ventuno") | `arduino:keyword_spotting` | `KeywordSpotting` |
| Speech-to-text | `arduino:asr` | `AutomaticSpeechRecognition` |
| Local LLM (NPU) | `arduino:llm` | `LargeLanguageModel` |
| Text-to-speech | `arduino:tts` | `TextToSpeech` |

No `bricks/` folder, no custom code to maintain.

## What changed vs. the Kokoro version

- **TTS**: `TextToSpeech` from `arduino.app_bricks.tts` replaces `KokoroTTS`.
  It plays directly through a `Speaker` peripheral, so the temporary `.wav`
  file and the `aplay` shell call are gone.
- **Lower latency**: the LLM reply is streamed and each finished sentence is
  queued with `tts.speak(sentence, block=False)`, so the assistant starts
  talking before the LLM has finished generating.
- **Clean shutdown**: `tts.cancel()` on errors, `tts.stop()` on exit.
- **Time zone** is a single constant (`TIME_ZONE`) at the top of `main.py`.
- The MCU sketch (LED matrix animations) is unchanged.

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

Derived from a GPL-3.0 project, so this version is GPL-3.0 as well.
