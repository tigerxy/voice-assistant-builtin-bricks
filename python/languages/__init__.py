"""Everything the assistant says or understands, one module per language.

To add a language, copy en.py to e.g. it.py, translate it and register it in LANGUAGES.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class Language:
    code: str                 # "en"
    name: str                 # "English"
    asr_language: str         # language for speech recognition (Whisper code)
    tts_model: str            # TTS model to set in app.yaml for this language
    wikipedia: str            # Wikipedia edition used by web_search

    system_prompt: str        # personality and speaking style of the LLM
    tools_prompt: str         # added to the system prompt when tools are on
    user_prompt: str          # wraps what the user said; fields: {now}, {part_of_day}, {text}
    lookup_results_prompt: str  # result of a lookup written as text; field: {results}

    # Things the assistant says (one is picked at random for variety)
    thinking_fillers: list
    search_fillers: list
    weather_fillers: list
    didnt_catch: list
    thanks_replies: list
    error_replies: list
    couldnt_find: str
    greetings: dict           # part of day -> replies to "hello"
    farewells: dict           # part of day -> goodbyes

    # Things the assistant understands
    exit_phrases: tuple       # end the conversation
    thanks_pattern: str       # regex on normalized text
    greeting_pattern: str     # regex on normalized text

    # Date and time, written out for the LLM
    part_of_day_names: dict   # "morning"/"afternoon"/"evening"/"night" -> word in this language
    weekdays: tuple
    months: tuple
    date_format: str          # fields: {weekday}, {day}, {month}, {year}, {time}
    time_format: str          # strftime format for {time}

    _thanks_re: re.Pattern = field(init=False, repr=False, compare=False)
    _greeting_re: re.Pattern = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        object.__setattr__(self, "_thanks_re", re.compile(self.thanks_pattern))
        object.__setattr__(self, "_greeting_re", re.compile(self.greeting_pattern))

    @staticmethod
    def normalize(text: str) -> str:
        """Lower case, letters/digits/apostrophes/spaces only (keeps umlauts)."""
        text = re.sub(r"[^\w' ]", "", text.lower().replace("’", "'"))
        return re.sub(r"\s+", " ", text).strip()

    def is_thanks(self, text: str) -> bool:
        return bool(self._thanks_re.match(self.normalize(text)))

    def is_greeting(self, text: str) -> bool:
        return bool(self._greeting_re.match(self.normalize(text)))

    def is_exit(self, text: str) -> bool:
        t = self.normalize(text)
        return any(t == p or t.startswith(p + " ") or t.endswith(" " + p) for p in self.exit_phrases)

    def format_now(self, now: datetime) -> str:
        return self.date_format.format(
            weekday=self.weekdays[now.weekday()],
            day=now.day,
            month=self.months[now.month - 1],
            year=now.year,
            time=now.strftime(self.time_format),
        )


def load_language(code: str) -> Language:
    from . import de, en

    languages = {"en": en.LANGUAGE, "de": de.LANGUAGE}
    if code not in languages:
        raise ValueError(f"Unknown language '{code}'. Available: {', '.join(languages)}")
    return languages[code]
