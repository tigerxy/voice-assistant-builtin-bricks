from . import Language

LANGUAGE = Language(
    code="en",
    name="English",
    asr_language="en",
    tts_model="piper-tts-en",
    wikipedia="en",

    system_prompt=(
        "You are a friendly voice assistant having a spoken conversation. "
        "Sound natural and warm, like a person: vary how you start your sentences, and feel free to use "
        "small interjections such as oh, hmm or well. Don't greet the user unless they greet you. "
        "Keep each answer brief and conversational, at most two or three sentences. "
        "Always answer in English. Everything you write is read aloud: write numbers, symbols and units "
        "as words, for example 86% becomes 86 percent, and never use emojis, markdown, lists or code. "
        "The conversation continues after your answer, so you may ask a short follow-up question when it helps."
    ),
    tools_prompt=(
        "\n\nYou have three tools. "
        "Use web_search to look up facts you are not sure about: people, places, things, or anything that may "
        "have changed recently. Use get_weather for weather questions. You get their result before you answer. "
        "Use show_emotion at most once per answer, when it has a clear feeling, to show a symbol on your LED face: "
        "heart for affection, happy for joy, sad for bad news, surprised for amazing facts, wink for jokes, "
        "angry when something is really unfair, confused when you don't understand, star for praise. "
        "Never mention the symbol or the tools in your answer. "
        "Don't use tools for small talk or things you know well."
    ),
    user_prompt="[System info: The current local time and date is {now} ({part_of_day})]\n\nUser: {text}",
    lookup_results_prompt=(
        "[Results of your actions]\n{results}\n\n"
        "Now answer my question in one or two short spoken sentences, using these results. "
        "Do not write web_search or get_weather again."
    ),

    thinking_fillers=["Hmm.", "Hmm, let me think.", "Good question.", "Let me see.", "Okay, one second."],
    search_fillers=["Let me look that up.", "One moment, I'll check.", "Hmm, let me search for that.", "Let me find out."],
    weather_fillers=["Let me check the weather.", "One moment, I'll look at the forecast."],
    didnt_catch=["Sorry, I didn't catch that.", "Hm? I didn't quite hear you.", "Sorry, could you say that again later?"],
    thanks_replies=["You're welcome!", "Anytime!", "Happy to help!", "My pleasure!", "No problem!"],
    error_replies=[
        "Oops, something went wrong on my side. Let's try that again later.",
        "Sorry, my head is a bit foggy right now. Please try again in a moment.",
    ],
    couldnt_find="Sorry, I couldn't find that out right now.",
    greetings={
        "morning": ["Good morning! What can I do for you?", "Hi! Nice to hear from you this morning. What's up?", "Hey! How can I help?"],
        "afternoon": ["Good afternoon! What can I do for you?", "Hi! Nice to hear from you this afternoon. What's up?", "Hey! How can I help?"],
        "evening": ["Good evening! What can I do for you?", "Hi! Nice to hear from you this evening. What's up?", "Hey! How can I help?"],
        "night": ["Hi! You're up late. What's on your mind?", "Hey there, night owl! What can I do for you?"],
    },
    farewells={
        "morning": ["Okay, talk to you later!", "Bye! Just call me if you need me.", "See you! Have a nice morning."],
        "afternoon": ["Okay, talk to you later!", "Bye! Just call me if you need me.", "See you! Have a nice afternoon."],
        "evening": ["Okay, talk to you later!", "Bye! Just call me if you need me.", "See you! Have a nice evening."],
        "night": ["Good night, sleep well!", "Okay, good night!"],
    },

    exit_phrases=("stop", "goodbye", "bye", "that's all", "that is all", "thank you that's it", "never mind"),
    thanks_pattern=r"^(ok(ay)? )?(thanks|thank you|thx|cheers)( (so|very) much| a lot)?( arduino)?$",
    greeting_pattern=r"^(hi|hello|hey|good (morning|afternoon|evening))( there| arduino)?$",

    part_of_day_names={"morning": "morning", "afternoon": "afternoon", "evening": "evening", "night": "night"},
    weekdays=("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
    months=("January", "February", "March", "April", "May", "June", "July",
            "August", "September", "October", "November", "December"),
    date_format="{time}, {weekday}, {month} {day}, {year}",
    time_format="%I:%M %p",
)
