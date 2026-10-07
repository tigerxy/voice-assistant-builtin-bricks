"""Part of the day, used for greetings, goodbyes and the LLM prompt."""


def part_of_day(hour: int) -> str:
    """'morning', 'afternoon', 'evening' or 'night'."""
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 18:
        return "afternoon"
    if 18 <= hour < 22:
        return "evening"
    return "night"


def is_night(hour: int, night_hours: tuple) -> bool:
    start, end = night_hours
    return hour >= start or hour < end
