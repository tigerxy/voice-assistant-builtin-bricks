from . import Language

LANGUAGE = Language(
    code="de",
    name="Deutsch",
    asr_language="de",
    tts_model="piper-tts-de",
    wikipedia="de",

    system_prompt=(
        "Du bist ein freundlicher Sprachassistent und führst ein gesprochenes Gespräch. "
        "Klinge natürlich und herzlich wie ein Mensch: Beginne deine Sätze unterschiedlich und benutze ruhig "
        "kleine Füllwörter wie oh, hm oder na ja. Begrüße den Nutzer nur, wenn er dich begrüßt. "
        "Antworte kurz und locker, mit höchstens zwei oder drei Sätzen, und duze den Nutzer. "
        "Antworte immer auf Deutsch. Alles, was du schreibst, wird vorgelesen: Schreibe Zahlen, Zeichen und "
        "Einheiten als Wörter aus, zum Beispiel wird 86% zu 86 Prozent, und benutze niemals Emojis, Markdown, "
        "Listen oder Code. Das Gespräch geht nach deiner Antwort weiter, du darfst also eine kurze Rückfrage "
        "stellen, wenn es passt."
    ),
    tools_prompt=(
        "\n\nDu hast drei Werkzeuge. "
        "Benutze web_search, um Fakten nachzuschlagen, bei denen du dir nicht sicher bist: Personen, Orte, Dinge "
        "oder alles, was sich kürzlich geändert haben könnte. Benutze get_weather für Fragen zum Wetter. "
        "Du bekommst das Ergebnis, bevor du antwortest. "
        "Benutze show_emotion höchstens einmal pro Antwort, wenn sie ein klares Gefühl hat, um ein Symbol auf "
        "deinem LED-Gesicht zu zeigen: heart für Zuneigung, happy für Freude, sad für schlechte Nachrichten, "
        "surprised für erstaunliche Fakten, wink für Witze, angry wenn etwas wirklich unfair ist, "
        "confused wenn du etwas nicht verstehst, star für Lob. "
        "Erwähne das Symbol oder die Werkzeuge nie in deiner Antwort. "
        "Benutze keine Werkzeuge für Smalltalk oder für Dinge, die du gut weißt."
    ),
    user_prompt="[Systeminfo: Es ist {now} ({part_of_day})]\n\nNutzer: {text}",
    lookup_results_prompt=(
        "[Ergebnisse deiner Aktionen]\n{results}\n\n"
        "Beantworte jetzt meine Frage in ein oder zwei kurzen gesprochenen Sätzen auf Deutsch, mit diesen "
        "Ergebnissen. Schreibe nicht noch einmal web_search oder get_weather."
    ),

    thinking_fillers=["Hm.", "Hm, lass mich kurz überlegen.", "Gute Frage.", "Mal sehen.", "Okay, einen Moment."],
    search_fillers=["Ich schau mal nach.", "Moment, ich prüfe das.", "Hm, da suche ich mal.", "Das finde ich heraus."],
    weather_fillers=["Ich schau mal aufs Wetter.", "Moment, ich prüfe die Vorhersage."],
    didnt_catch=["Entschuldigung, das habe ich nicht verstanden.", "Hm? Ich habe dich nicht richtig gehört.",
                 "Sorry, sag das gern später nochmal."],
    thanks_replies=["Gern geschehen!", "Immer gern!", "Kein Problem!", "Gerne!", "Hab ich gern gemacht!"],
    error_replies=[
        "Hoppla, da ist bei mir etwas schiefgegangen. Versuchen wir es später nochmal.",
        "Entschuldigung, ich bin gerade etwas durcheinander. Versuch es gleich nochmal.",
    ],
    couldnt_find="Tut mir leid, das konnte ich gerade nicht herausfinden.",
    greetings={
        "morning": ["Guten Morgen! Was kann ich für dich tun?", "Hallo! Schön, heute Morgen von dir zu hören. Was gibt's?", "Hey! Wie kann ich helfen?"],
        "afternoon": ["Guten Tag! Was kann ich für dich tun?", "Hallo! Schön, von dir zu hören. Was gibt's?", "Hey! Wie kann ich helfen?"],
        "evening": ["Guten Abend! Was kann ich für dich tun?", "Hallo! Schön, heute Abend von dir zu hören. Was gibt's?", "Hey! Wie kann ich helfen?"],
        "night": ["Hallo! Du bist ja noch spät wach. Was gibt's?", "Hey, du Nachteule! Was kann ich für dich tun?"],
    },
    farewells={
        "morning": ["Okay, bis später!", "Tschüss! Ruf mich einfach, wenn du mich brauchst.", "Bis dann! Einen schönen Vormittag noch."],
        "afternoon": ["Okay, bis später!", "Tschüss! Ruf mich einfach, wenn du mich brauchst.", "Bis dann! Einen schönen Nachmittag noch."],
        "evening": ["Okay, bis später!", "Tschüss! Ruf mich einfach, wenn du mich brauchst.", "Bis dann! Einen schönen Abend noch."],
        "night": ["Gute Nacht, schlaf gut!", "Okay, gute Nacht!"],
    },

    exit_phrases=("stopp", "stop", "tschüss", "tschüs", "auf wiedersehen", "das wars", "das war's",
                  "das war alles", "danke das wars", "vergiss es", "ende"),
    thanks_pattern=r"^(ok(ay)? )?(danke|dankeschön|danke schön|vielen dank|merci)( dir| sehr| vielmals)?( arduino)?$",
    greeting_pattern=r"^(hallo|hi|hey|servus|moin|guten (morgen|tag|abend))( du| arduino)?$",

    part_of_day_names={"morning": "Morgen", "afternoon": "Nachmittag", "evening": "Abend", "night": "Nacht"},
    weekdays=("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"),
    months=("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
            "August", "September", "Oktober", "November", "Dezember"),
    date_format="{weekday}, der {day}. {month} {year}, {time}",
    time_format="%H:%M Uhr",
)
