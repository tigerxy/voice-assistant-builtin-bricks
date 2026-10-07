"""The LLM: turns what the user said into a spoken answer, using tools when needed."""

import threading

from .textcalls import LOOKUP_TOOLS, extract_tool_calls, split_speakable
from .timeofday import part_of_day


class Brain:
    def __init__(self, llm, tools, expression, language, settings, now):
        self.llm = llm
        self.tools = tools
        self.expression = expression
        self.language = language
        self.settings = settings
        self.now = now  # function returning the current local datetime

    def answer(self, text: str) -> None:
        """Answer the user: stream the LLM reply, look things up if it asks for it, speak it."""
        self.expression.new_turn()
        now = self.now()
        prompt = self.language.user_prompt.format(
            now=self.language.format_now(now),
            part_of_day=self.language.part_of_day_names[part_of_day(now.hour)],
            text=text,
        )

        # If the model is slow to start, say "hmm..." like a person thinking
        filler_timer = threading.Timer(
            self.settings.thinking_filler_seconds,
            lambda: self.expression.filler(self.language.thinking_fillers),
        )
        filler_timer.daemon = True
        filler_timer.start()
        try:
            for lookup_round in range(self.settings.max_lookup_rounds + 1):
                lookups = self._answer_round(prompt)
                if not lookups:
                    break
                if lookup_round == self.settings.max_lookup_rounds:
                    self.expression.say(self.language.couldnt_find)
                    break
                prompt = self._run_lookups(lookups)
        finally:
            filler_timer.cancel()

        self.expression.finish_turn()

    def forget(self) -> None:
        """Start the next conversation without the history of this one."""
        self.llm.clear_memory()

    def _answer_round(self, prompt):
        """Stream one LLM reply: speak its sentences, run emotions, collect lookups written as text.

        Tool calls the model returns properly are run by the LLM brick itself while streaming.
        """
        lookups = []

        def handle(piece):
            had_lookups = bool(lookups)
            speech, calls = extract_tool_calls(piece)
            for name, args in calls if self.settings.use_tools else []:
                if name not in LOOKUP_TOOLS:
                    self.tools.run(name, args)
                elif (name, args) not in lookups:
                    lookups.append((name, args))
            if speech and not had_lookups:
                self.expression.say(speech)

        print("🧠 AI thinking (local): ", end="", flush=True)
        buffer = ""
        stream = self.llm.chat_stream(prompt)
        try:
            for chunk in stream:
                print(chunk, end="", flush=True)
                buffer += chunk
                # Hand every finished sentence to the speech queue right away,
                # so speech starts before the LLM has finished generating.
                pieces, buffer = split_speakable(buffer)
                for piece in pieces:
                    handle(piece)
                if lookups:
                    break  # the model asked for data: whatever it writes next would be made up
            else:
                if buffer.strip():
                    handle(buffer)
        finally:
            stream.close()
        print()
        return lookups

    def _run_lookups(self, lookups):
        """Run lookups the LLM wrote as text; the results become the next message for it."""
        results = [f"{name}({args}):\n{self.tools.run(name, args)}" for name, args in lookups[:3]]
        return self.language.lookup_results_prompt.format(results="\n\n".join(results))
