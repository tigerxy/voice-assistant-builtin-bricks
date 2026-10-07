"""The tools the LLM can use: web search, weather forecast and emotions on the LED face.

They are handed to the LLM brick as plain functions (tools=...): the brick wraps
each one into a LangChain tool from its name, type hints and docstring. The
docstrings are written for the LLM and stay in English for every language.
"""

import requests

from arduino.app_bricks.weather_forecast import WeatherForecast

from .face import EMOTIONS
from .textcalls import arg


class Tools:
    def __init__(self, expression, language, settings, weather=None, http=None):
        self.expression = expression
        self.language = language
        self.settings = settings
        self.weather = weather or WeatherForecast()
        self.http = http or requests.Session()
        self.http.headers["User-Agent"] = "ArduinoVoiceAssistant/1.0 (offline voice assistant demo)"

        # Plain functions (not methods), as the LLM brick expects them
        def web_search(query: str) -> str:
            """Search the internet for facts and background information.

            Args:
                query: A short search query with the key words, for example "Eiffel Tower height".

            Returns:
                Text snippets from the search results.
            """
            return self._web_search(query)

        def get_weather(city: str, days_ahead: int = 0) -> str:
            """Get the weather forecast for a city.

            Args:
                city: City name, for example "Berlin".
                days_ahead: 0 for today, 1 for tomorrow, up to 6.

            Returns:
                A short description of the expected weather.
            """
            return self._get_weather(city, days_ahead)

        def show_emotion(emotion: str) -> str:
            """Show an emotion symbol on the assistant's LED matrix face while it answers.

            Args:
                emotion: One of "heart", "happy", "sad", "surprised", "wink", "angry", "confused", "star".

            Returns:
                Confirmation that the symbol is shown.
            """
            return self._show_emotion(emotion)

        self.web_search = web_search
        self.get_weather = get_weather
        self.show_emotion = show_emotion

    def for_llm(self) -> list:
        """The list for LargeLanguageModel(tools=...)."""
        return [self.web_search, self.get_weather, self.show_emotion]

    def run(self, name: str, args: dict) -> str:
        """Run a tool call that the LLM wrote as text (see textcalls.py)."""
        if name == "web_search":
            return self.web_search(str(arg(args, "query", "q", "text")))
        if name == "get_weather":
            return self.get_weather(str(arg(args, "city", "location", "place")),
                                    arg(args, "days_ahead", "days", "day", default=0))
        if name == "show_emotion":
            return self.show_emotion(str(arg(args, "emotion", "name", "symbol")))
        return f"Unknown tool '{name}'."

    # --- implementations --------------------------------------------------------------
    def _web_search(self, query):
        print(f"\n🔎 Searching the web for: {query}")
        self.expression.filler(self.language.search_fillers)
        found = []
        for source in (self._duckduckgo, self._wikipedia):
            try:
                text = source(query)
                if text:
                    found.append(text)
            except Exception as e:
                print(f"⚠️ Search source failed: {e}")
        if not found:
            return "No results found."
        return "\n\n".join(found)[:2000]  # keep the context small for the local model

    def _get_weather(self, city, days_ahead):
        try:
            days_ahead = max(0, min(int(days_ahead), 6))
        except (TypeError, ValueError):
            days_ahead = 0
        print(f"\n🌦️ Getting weather for {city} (+{days_ahead} days)")
        self.expression.filler(self.language.weather_fillers)
        try:
            data = self.weather.get_forecast_by_city(
                city, timezone=str(self.settings.time_zone), forecast_days=days_ahead + 1)
            return f"Weather in {city}: {data.description} ({data.category})."
        except Exception as e:
            return f"Weather lookup failed: {e}"

    def _show_emotion(self, emotion):
        name = str(emotion).strip().strip("'\"").lower()
        if not self.expression.show_emotion(name):
            return f"Unknown emotion '{emotion}'. Use one of: {', '.join(EMOTIONS)}."
        print(f"\n😀 Showing emotion: {name}")
        return f"The {name} symbol is now shown. Now give your spoken answer."

    def _duckduckgo(self, query):
        r = self.http.get(
            "https://api.duckduckgo.com/",
            params={"q": query, "format": "json", "no_html": 1, "skip_disambig": 1},
            timeout=self.settings.search_timeout,
        )
        r.raise_for_status()
        data = r.json()
        parts = [data.get("Answer"), data.get("AbstractText")]
        parts += [t.get("Text") for t in data.get("RelatedTopics", [])[:3] if isinstance(t, dict)]
        return "\n".join(p for p in parts if p)

    def _wikipedia(self, query):
        base = f"https://{self.language.wikipedia}.wikipedia.org"
        r = self.http.get(
            f"{base}/w/api.php",
            params={"action": "query", "list": "search", "srsearch": query, "srlimit": 3, "format": "json"},
            timeout=self.settings.search_timeout,
        )
        r.raise_for_status()
        results = []
        for hit in r.json().get("query", {}).get("search", [])[:2]:
            title = hit["title"]
            s = self.http.get(f"{base}/api/rest_v1/page/summary/{requests.utils.quote(title)}",
                              timeout=self.settings.search_timeout)
            if s.ok and s.json().get("extract"):
                results.append(f"{title}: {s.json()['extract']}")
        return "\n\n".join(results)
