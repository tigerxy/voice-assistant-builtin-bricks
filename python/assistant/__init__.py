"""The voice assistant, split into small parts:

    ears.py          microphone, wake word and speech recognition
    brain.py         the LLM: answers, tool calls, lookups
    tools.py         web search, weather and emotions for the LLM
    textcalls.py     tool calls the LLM writes as text (and cleaning text for speech)
    voice.py         speech queue on top of the TTS brick
    face.py          the LED matrix (sketch.ino)
    sounds.py        chimes and jingles
    expression.py    speech + face + sounds together, as one "personality"
    conversation.py  the conversation flow (state machine)
    timeofday.py     morning / afternoon / evening / night
    setup.py         creates and connects everything

All texts live in ../languages, all settings in ../config.py.
"""
