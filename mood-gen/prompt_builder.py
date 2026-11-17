import re
from typing import Iterable

BASE_STYLE = (
    "oil painting, Van Gogh swirling impasto brushstrokes, "
    "Edvard Munch expressionist emotion, "
    "Paul Cezanne structural color facets, abstract figurative"
)

_EMOJI_CUES = {
    "👶": "newborn dawn",
    "🎒": "school pathway",
    "🎸": "acoustic guitar music",
    "📚": "stacked books study",
    "🎓": "graduation celebration",
    "💼": "city work architecture",
    "🚀": "ascending rocket skyward",
    "🌧️": "heavy rainstorm",
    "🌧": "heavy rainstorm",
    "⛈️": "thunderstorm clouds",
    "🌞": "bright sun",
    "☀️": "radiant sunlight",
    "✨": "shimmering starlight",
    "🤖": "mechanical robot",
    "🐶": "loyal dog",
    "🐱": "quiet cat",
    "🐺": "lone wolf",
    "🦊": "alert fox",
    "🐦": "flying bird",
    "🦉": "night owl",
    "❤️": "warm heart",
    "💔": "fractured heart",
    "🌿": "green foliage",
    "🏔️": "mountain ridge",
    "🌊": "churning ocean waves",
}

_ROBOT_KEYWORDS = (
    "robot",
    "robotics",
    "android",
    "automaton",
    "cyborg",
    "mech",
    "machine",
    "ai ",
    "bot",
    "rocket",
)

_ANIMAL_KEYWORDS = (
    "animal",
    "dog",
    "cat",
    "bird",
    "wolf",
    "fox",
    "bear",
    "horse",
    "owl",
    "fish",
    "pet",
    "wildlife",
    "deer",
)

_HUMANOID_KEYWORDS = (
    "human",
    "humanoid",
    "person",
    "people",
    "friend",
    "family",
    "child",
    "kid",
    "birth",
    "school",
    "graduat",
    "partner",
    "crowd",
    "walk",
    "team",
    "alone",
    "portrait",
)


def _sanitize_text(value: object, max_len: int = 180) -> str:
    if value is None:
        return ""
    text = str(value)
    text = re.sub(r"[\r\n\t\x00-\x1f\x7f]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_len:
        text = text[: max_len - 3].rstrip() + "..."
    return text


def _mood_atmosphere(mood: float) -> str:
    try:
        score = float(mood)
    except (TypeError, ValueError):
        score = 5.0
    score = max(0.0, min(10.0, score))

    if score < 4.0:
        return (
            "stormy indigo and deep slate atmosphere, heavy expressive shadows, "
            "turbulent swirling sky"
        )
    if score < 7.0:
        return (
            "earthy ochre and muted sage atmosphere, balanced geometric horizon, "
            "contemplative tonal harmony"
        )
    return (
        "luminous gold and vibrant cobalt atmosphere, radiant sunlit motion, "
        "uplifting chromatic energy"
    )


def _extract_motifs(combined_lower: str, emoji: str) -> list[str]:
    motifs: list[str] = []
    if any(kw in combined_lower for kw in _ROBOT_KEYWORDS) or "🤖" in emoji or "🚀" in emoji:
        motifs.append("abstract geometric robot figure")
    if any(kw in combined_lower for kw in _ANIMAL_KEYWORDS) or any(
        e in emoji for e in ("🐶", "🐱", "🐺", "🦊", "🐦", "🦉", "🐾")
    ):
        motifs.append("expressive symbolic animal silhouette")
    if any(kw in combined_lower for kw in _HUMANOID_KEYWORDS) or any(
        e in emoji for e in ("👶", "🎒", "🎓", "💼", "🧑", "👨", "👩", "🤝")
    ):
        motifs.append("abstract humanoid figure")

    if not motifs:
        motifs.append("subtle symbolic humanoid and companion animal silhouettes")
    return motifs


def build_mood_prompt(mood: float, emoji: str, events: list, notes: str) -> str:
    """Build an expressionist image generation prompt from slide fields."""
    emoji_str = _sanitize_text(emoji, max_len=16)
    emoji_cue = _EMOJI_CUES.get(emoji_str, "")

    if isinstance(events, str):
        event_items: Iterable[object] = [events]
    elif isinstance(events, Iterable):
        event_items = events
    else:
        event_items = []

    clean_events = [s for s in (_sanitize_text(item, max_len=80) for item in event_items) if s]
    clean_notes = _sanitize_text(notes, max_len=140)

    parts = [BASE_STYLE, _mood_atmosphere(mood)]

    combined_lower = " ".join([emoji_cue, *clean_events, clean_notes]).lower()
    motifs = _extract_motifs(combined_lower, emoji_str)
    parts.append(", ".join(motifs))

    narrative_bits = []
    if emoji_cue:
        narrative_bits.append(emoji_cue)
    if clean_events:
        narrative_bits.append("; ".join(clean_events[:3]))
    if clean_notes:
        narrative_bits.append(clean_notes)

    if narrative_bits:
        parts.append("inspired by " + ", ".join(narrative_bits))

    return ", ".join(parts)
