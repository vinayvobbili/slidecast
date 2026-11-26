"""Text-to-speech providers.

A provider is anything with::

    synthesize(text: str, out_path: Path) -> float | None

It writes an audio file to ``out_path`` and returns the clip duration in seconds,
or ``None`` if it can't measure it (e.g. it emitted MP3 and you don't want a
probe dependency). When the duration is unknown, the video builder lets the audio
drive the segment length (ffmpeg ``-shortest``) instead of padding to a target.

Five providers ship in the box:

* :class:`KokoroTTS` — any OpenAI-compatible ``/v1/audio/speech`` endpoint
  (Kokoro, OpenAI, LocalAI, …). Defaults to WAV so duration is measurable.
* :class:`MLXKokoroTTS` — Kokoro run in-process on Apple Silicon via
  ``mlx-audio``: no server, WAV.
* :class:`GTTSTTS` — Google Translate TTS via the ``gtts`` package (MP3, no
  measurable duration → ``-shortest``).
* :class:`MacSayTTS` — macOS's built-in ``say``: offline, no dependencies, WAV.
* :class:`SilentTTS` — a silent track of a fixed length. No dependencies; used
  for silent slides, muted reels, and deterministic tests.

Bring your own by implementing the same one-method shape.
"""

from __future__ import annotations

import os
import re
import struct
import wave
from array import array
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class TTSProvider(Protocol):
    def synthesize(self, text: str, out_path: Path) -> float | None:
        """Write audio for ``text`` to ``out_path``; return its duration or None."""
        ...


def wav_duration(path: Path) -> float | None:
    """Duration of a WAV file in seconds, or None if it isn't a readable WAV."""
    try:
        with wave.open(str(path)) as w:
            rate = w.getframerate()
            return w.getnframes() / float(rate) if rate else None
    except Exception:
        return None


def apply_phonetic(text: str, rules: dict[str, str] | None) -> str:
    """Rewrite spoken text by regex so TTS pronounces tricky tokens correctly.

    ``rules`` maps a regex pattern to its spoken replacement, e.g.
    ``{r"\\bSOC\\b": "sock"}`` so "SOC" is said as a word, not spelled out. The
    on-screen slide text is untouched — this only changes the audio.
    """
    if not rules:
        return text
    for pattern, repl in rules.items():
        text = re.sub(pattern, repl, text)
    return text


# Kokoro says a plural acronym written as one word ("PDFs", "APIs", "1099s") naturally: the letters, then a z. A
# spelled-out one ("P D Fs", "P D F s", "A P Is") comes out as the letters, then the letter S: "P D F S". So a
# spelled-out plural is joined back into its word. Two shapes: single characters with spaces between them (two
# or more, at least one a letter) then s, with or without an apostrophe; or one 2-6 character token and a stray
# " s" ("PDF s"). Hyphenated forms ("W-2s") are already fine and left alone.
_SPACED_PLURAL = re.compile(
    r"(?<![\w'’-])(?:(?=[0-9 ]*[A-Z])(?P<spaced>[A-Z0-9](?: [A-Z0-9]){1,5}) ?['’]?"
    r"|(?=[0-9]*[A-Z])(?P<token>[A-Z0-9]{2,6}) )s(?![\w'’-])"
)


def normalize_acronym_plurals(text: str) -> str:
    """Join a spelled-out plural acronym back into one word, so the voice says "PDFs" and not "P D F S".

    "P D Fs", "P D F s", "P D F's", "A P Is", "W 2 s" and "PDF s" become "PDFs", "APIs", "W2s". Anything
    else, including a lone "A" or "I" before a word and a spelled-out singular ("P D F"), is left alone.
    """
    return _SPACED_PLURAL.sub(lambda m: (m.group("spaced") or m.group("token")).replace(" ", "") + "s", text)


def apply_pronunciations(text: str, pronunciations: dict[str, str] | None) -> str:
    """Replace whole words by what the voice should say, e.g. ``{"TINs": "tee eye ens"}``.

    A plain dictionary, not regexes (that is ``phonetic``): each key is matched exactly, case and all, as a
    whole word, the longest key first, and its value is spoken as written. The on-screen text is untouched.
    """
    if not pronunciations:
        return text
    keys = sorted(pronunciations, key=len, reverse=True)
    pattern = r"(?<![\w-])(?:" + "|".join(re.escape(k) for k in keys) + r")(?![\w-])"
    return re.sub(pattern, lambda m: pronunciations[m.group(0)], text)


def _pronounce(text: str, phonetic, acronym_plurals: bool, pronunciations) -> str:
    """What the voice is given before any engine fixes: the author's rules, the plural fix, then the dictionary."""
    text = apply_phonetic(text, phonetic)
    if acronym_plurals:
        text = normalize_acronym_plurals(text)
    return apply_pronunciations(text, pronunciations)


# Words Kokoro says with the wrong one of their two sounds, fixed with misaki's own override, "[word](/phonemes/)".
# Its tagger reads "went live" as the verb ("liv"): live after these words, or before a noun it describes, gets the
# adjective's sound ("lyve"). "I live in" is left to the tagger, which gets the verb right.
_LYVE = r"[\g<w>](/lˈIv/)"
KOKORO_FIXES: dict[str, str] = {
    r"(?i)\b(?P<pre>(?:go|goes|going|went|gone|is|are|was|were|be|been|it's|that's|what's|ran it|run it|"
    r"running it)\s+)(?P<w>live)\b": r"\g<pre>" + _LYVE,
    r"(?i)(?<!\[)\b(?P<w>live)(?=\s+(?:runs?|tests?|systems?|data|demos?|traffic|calls?|customers?|users?|"
    r"environments?|incidents?|alerts?|feeds?|sites?|versions?|coding|streams?|events?)\b)": _LYVE,
    # "dive" with the long I (rhymes with "hive"), as in "deep dives"; Kokoro says "DEE-vees" for the plural
    r"(?i)(?<![\[\w])(?P<w>dives)\b": r"[\g<w>](/dˈIvz/)",
    r"(?i)(?<![\[\w])(?P<w>dive)\b": r"[\g<w>](/dˈIv/)",
    r"(?i)(?<![\[\w])(?P<w>dived)\b": r"[\g<w>](/dˈIvd/)",
    r"(?i)(?<![\[\w])(?P<w>diving)\b": r"[\g<w>](/dˈIvɪŋ/)",
    # Names said as words, which Kokoro would spell out letter by letter
    r"(?i)\bJSON\b": "Jason",
    r"\bPyPI\b": "pie pee eye",
    # Acronyms said letter by letter, which Kokoro would run together as one word ("aawss")
    r"\bAWS\b": "A W S",
    r"\bGCP\b": "G C P",
    r"\bEDR\b": "E D R",
    r"\bIAM\b": "I A M",
    # Lowercase tech words misaki can't read: "id" is the Freudian word, "url"/"api"/"ttl" come out as nothing.
    # Uppercased they are spelled out ("I D"). "I'd" and words that merely contain these letters are left alone.
    r"\b[Ii]d(?P<s>s?)\b": r"ID\g<s>",
    r"\burl(?P<s>s?)\b": r"URL\g<s>",
    r"\bapi(?P<s>s?)\b": r"API\g<s>",
    r"\bttl(?P<s>s?)\b": r"TTL\g<s>",
    # Ticket ids like US12345: alone at the start misaki says the word "us"
    r"\bUS(?=\d)": "U S ",
    # "24x7", "24 x 7", "1280×800": number, x, number is "24 by 7", not the letter x. "2x faster" (times) and hex
    # like 0x1F are left alone, as is anything inside a word or identifier, and "6 × 24 h" (times, a unit follows)
    r"(?<![\w.])(?!0[xX])(?P<a>\d+)\s?[x×]\s?(?P<b>\d+)"
    r"(?!\w|\s(?:h|hrs?|hours?|m|mins?|s|secs?|ms|d|days?)\b)": r"\g<a> by \g<b>",
    # "route" the American way (rhymes with "out"), not the British "root"; whole words, so "routine" stays
    r"\b(?P<r>[Rr])oute(?P<s>s?)\b": r"\g<r>owt\g<s>",
    r"\b(?P<r>[Rr])outed\b": r"\g<r>owted",
    r"\b(?P<r>[Rr])outing\b": r"\g<r>owting",
    r"\b(?P<r>[Rr])outer(?P<s>s?)\b": r"\g<r>owter\g<s>",
}

# "read" is "reed" now and "red" in the past, and Kokoro's tagger can't tell them apart. The rules below pick the
# sound from the words around it and hand it to misaki as a phoneme override, which keeps the written word. Order
# matters: an author's {read:past} first, then past cues, then present cues; a word already rewritten (it then sits
# in "[...]") is skipped by the later rules.
#   past:    after have/has/had/'ve/'d/was/were/been/is/are/be/being/get/got/already/just/never/well/widely/
#            recently/once, or with yesterday, ago, earlier or "last week"-style words within four words after it
#   present: after to/will/can/do/please/I/you/we/they and so on, and an imperative at the start of a sentence
#   neither: left to Kokoro's own guess
_READ_FORMS = {  # spelling regex -> (past phonemes, present phonemes)
    r"read": ("ɹˈɛd", "ɹˈid"),
    r"re-?read": ("ɹˌiɹˈɛd", "ɹˌiɹˈid"),
    r"proof-?read": ("pɹˈufɹˌɛd", "pɹˈufɹˌid"),
    r"mis-?read": ("mɪsɹˈɛd", "mɪsɹˈid"),
}
_READ_PAST_CUES = (r"(?:\b(?:have|has|had|was|were|been|is|are|be|being|get|got|already|just|never|well|widely|"
                   r"recently|once)|['’](?:ve|d))\s+")
_READ_PAST_TIME = (r"(?:yesterday|ago|last\s+(?:night|week|weekend|month|quarter|year|decade|time)|"
                   r"earlier(?=\s+(?:today|this|that|on|in)\b|\s*[.,;:!?]|\s*$))")
_READ_PRESENT_CUES = (r"\b(?:to|will|would|can|cannot|could|should|may|might|must|shall|do|does|did|please|"
                      r"(?:don|doesn|didn|won|can)['’]t|let['’]s|i|you|we|they)\s+")
# "I read" alone is a guess, so the rest of the sentence decides it first: a habit ("every morning", "daily") makes it
# the present; else another verb in the past ("I read the logs and found it", "When it fired, I read the logs") makes
# it the past. A past verb is an -ed word (not "need", "hundred" and the like) or a common irregular one.
_READ_HABIT = (r"(?:every|each)\s+(?:day|morning|night|week|weekday|month|time|shift|sprint|quarter)|daily|weekly|"
               r"usually|always|often|routinely|regularly|nowadays|these\s+days")
_PAST_VERB = (r"(?:(?!(?:hundred|embed|kindred|sacred|naked|wicked|rugged|ragged|crooked|beloved|seabed|hatred)\b)"
              r"[a-z]{2,}(?<!e)ed|was|were|had|did|found|saw|went|took|made|built|wrote|led|ran|sent|got|told|said|"
              r"knew|thought|came|gave|began|brought|caught|felt|kept|left|lost|met|paid|spent|spoke|stood|"
              r"understood|won|chose|drove|fell|grew|heard|held|sold|taught|threw|wore)")
_SENTENCE = r"[^.!?;\[\]]*?"  # more of the same sentence, short of a word a rule already rewrote
_SENTENCE_START = r"(?:^|(?<=[.!?]\s))"


def _read_rules() -> dict[str, str]:
    override, past, habit, story, present = {}, {}, {}, {}, {}
    for form, (red, reed) in _READ_FORMS.items():
        word = rf"(?P<w>{form})\b"
        for tense, ph in (("past", red), ("present", reed)):
            override[rf"(?i)\{{(?P<w>{form}):{tense}\}}"] = rf"[\g<w>](/{ph}/)"
        past[rf"(?i)(?P<pre>{_READ_PAST_CUES}){word}"] = rf"\g<pre>[\g<w>](/{red}/)"
        past[rf"(?i)(?<=[\w'’]\s)\b{word}(?=(?:\s+[\w'’-]+){{0,4}}?\s+{_READ_PAST_TIME})"] = rf"[\g<w>](/{red}/)"
        habit[rf"(?i)(?<![\[\w-]){word}(?={_SENTENCE}\b(?:{_READ_HABIT})\b)"] = rf"[\g<w>](/{reed}/)"
        habit[rf"(?im)(?P<pre>{_SENTENCE_START}{_SENTENCE}\b(?:{_READ_HABIT})\b{_SENTENCE})(?<![\[\w-]){word}"] = (
            rf"\g<pre>[\g<w>](/{reed}/)")
        story[rf"(?i)(?P<pre>\b(?:i|we|you|they)\s+){word}(?={_SENTENCE}(?:\b(?:and|then|but|so|before|after|until|"
              rf"when|once)|,)\s+(?:(?:i|we|you|they|he|she|it|which|that|this)\s+)?{_PAST_VERB}\b)"] = (
            rf"\g<pre>[\g<w>](/{red}/)")
        story[rf"(?im)(?P<pre>{_SENTENCE_START}{_SENTENCE}\b{_PAST_VERB}\b{_SENTENCE}\b(?:i|we|you|they)\s+){word}"] = (
            rf"\g<pre>[\g<w>](/{red}/)")
        present[rf"(?i)(?P<pre>{_READ_PRESENT_CUES}){word}"] = rf"\g<pre>[\g<w>](/{reed}/)"
        present[rf"(?im){_SENTENCE_START}{word}"] = rf"[\g<w>](/{reed}/)"
    # "read-only" and its kin are always the present, even after "is"/"are" ("the tools are read-only"), which would
    # otherwise count as a past cue; these rules run first so the cue rules find the word already rewritten
    compound = {r"(?i)(?P<w>read)(?=-(?:only|write|access)\b)": r"[\g<w>](/ɹˈid/)"}
    return {**override, **compound, **past, **habit, **story, **present}


KOKORO_FIXES.update(_read_rules())

# "resume" is the verb (ri-ZOOM) or the noun (REZ-oo-may); Kokoro always says the verb, and in a job-search tool the
# noun is nearly always meant. So it is the noun unless a word before or after makes it the verb: to/will/can/... or
# "not" before it, or "resume where/from/the/once/..." after it. The verb rules go first; a word already rewritten
# (it then sits in "[...]") is skipped by the noun rules. "resumed" and "resuming" are only verbs: left alone.
_RESUME_VERB_BEFORE = (r"\b(?:to|will|would|can|cannot|could|should|may|might|must|shall|do|does|did|not|then|"
                       r"(?:don|doesn|didn|won|can|couldn|shouldn)['’]t|let['’]s)\s+")
_RESUME_VERB_AFTER = (r"(?=\s+(?:where|from|the|once|after|when|work|working|playing|"
                      r"normal|operations?|service|automatically)\b)")
KOKORO_FIXES.update({
    rf"(?i)(?P<pre>{_RESUME_VERB_BEFORE})(?P<w>resume)\b": r"\g<pre>[\g<w>](/ɹəzˈum/)",
    rf"(?i)(?<![\[\w])(?P<w>resumes){_RESUME_VERB_AFTER}": r"[\g<w>](/ɹəzˈumz/)",
    rf"(?i)(?<![\[\w])(?P<w>resume){_RESUME_VERB_AFTER}": r"[\g<w>](/ɹəzˈum/)",
    r"(?i)(?<![\[\w])(?P<w>r[eé]sum[eé])\b": r"[\g<w>](/ɹˈɛzəmˌA/)",
    r"(?i)(?<![\[\w])(?P<w>r[eé]sum[eé]s)\b": r"[\g<w>](/ɹˈɛzəmˌAz/)",
})

# "README" is two words, "read me" (present tense): Kokoro spells README out as letters and can't read lowercase
# "readme" at all. Plurals and possessives take the "-z" ending. "README.md" keeps its extension, said "dot M D",
# since the bracket override would otherwise swallow the ".md".
_README, _READMES = "/ɹˈid mˈi/", "/ɹˈid mˈiz/"
KOKORO_FIXES.update({
    r"(?i)(?<![\[\w])(?P<w>readme)\.md\b": rf"[\g<w>]({_README}) dot M D",
    r"(?i)(?<![\[\w])(?P<w>readme)(?P<s>['’]s)(?!\w)": rf"[\g<w>\g<s>]({_READMES})",
    r"(?i)(?<![\[\w])(?P<w>readmes)\b": rf"[\g<w>]({_READMES})",
    r"(?i)(?<![\[\w])(?P<w>readme)\b": rf"[\g<w>]({_README})",
})

# "epoch" is "EP-ock" and "epochs" "EP-ocks". misaki gives it "ˈɛpək", with a schwa, which Kokoro says so softly it
# is heard as "epic" (Whisper writes "epic" for it). The full vowel in the second syllable is heard as "epoch".
KOKORO_FIXES.update({
    r"(?i)(?<![\[\w])(?P<w>epoch['’]s)(?!\w)": r"[\g<w>](/ˈɛpɑks/)",
    r"(?i)(?<![\[\w])(?P<w>epochs)\b": r"[\g<w>](/ˈɛpɑks/)",
    r"(?i)(?<![\[\w])(?P<w>epoch)\b": r"[\g<w>](/ˈɛpɑk/)",
})

# Heteronyms misaki's tagger gets wrong, one row each: (word, phonemes, sound name, words before, words after). A row
# gives the word its phonemes when the text before it ends with ``before`` and the text after it starts with ``after``
# (regexes, case ignored; "" matches anything). Rows are tried in order and a word already given a sound is skipped,
# so a narrow row goes ahead of a broad one for the same word. Only what misaki says wrong has a row: it already gets
# "use", "record", "present", "wind", "live in" and "the lead engineer" right (the tests pin that down).
_NOUN_AFTER = r"\s+(?:{})\b"
HETERONYMS: list[tuple[str, str, str, str, str]] = [
    # lead the metal ("led"), not the verb or the role ("leed"), which misaki always says
    ("lead", "lˈɛd", "metal", "",
     r"[\s-]+(?:pipes?|paint|poisoning|based|free|acid|exposure|shot|weights?|aprons?|bullets?|crystal|oxide|"
     r"contamination|solder|batter(?:y|ies)|and\s+(?:copper|tin|zinc|mercury|arsenic|cadmium))\b"),
    ("lead", "lˈɛd", "metal", r"\b(?:made\s+of|heavy\s+as|full\s+of|traces\s+of|amounts\s+of|levels\s+of)\s+",
     r"\s*(?:[.,;:!?)]|$)"),
    # tear the rip ("tare"), not the drop from an eye ("teer"), which misaki says for the noun
    ("tear", "tˈɛɹ", "rip", "", r"\s+in\s+(?:the|a|an|its)\s+(?!eyes?\b)"),
    ("tears", "tˈɛɹz", "rip", "", r"\s+in\s+(?:the|a|an|its)\s+(?!eyes?\b)"),
    # bass the sound ("base"), unless it's the fish; misaki always says the fish
    ("bass", "bˈæs", "fish", r"\b(?:sea|striped|largemouth|smallmouth|spotted|caught|catch|catching)\s+(?:a\s+)?", ""),
    ("bass", "bˈæs", "fish", "", _NOUN_AFTER.format("fish|fishing|boats?|lakes?|ponds?|tournaments?|lures?|anglers?")),
    ("bass", "bˈAs", "music", "", ""),
    # minute as in tiny ("my-NOOT") before what it describes; misaki gives "minute changes" the time ("MIN-it")
    ("minute", "mInˈut", "tiny", r"(?<!last\s)",
     _NOUN_AFTER.format("changes?|differences?|details?|amounts?|traces?|quantities|particles?|variations?|"
                        "adjustments?|fractions?|errors?|discrepanc(?:y|ies)")),
    # content the adjective ("con-TENT") after a linking word and at the end of its phrase; else the noun ("CON-tent"),
    # which misaki misses at the start of a sentence ("Content moderation")
    ("content", "kəntˈɛnt", "adjective",
     r"(?:\b(?:am|is|are|was|were|be|been|being|seem|seems|seemed|feel|feels|felt|remain|remains|remained|stay|"
     r"stays|stayed|quite|very|perfectly|fairly|not|more|less|so|i|we|you|they|he|she)|['’](?:m|re))\s+",
     r"\s+(?:with|to)\b|\s*(?:[.,;:!?]|$)"),
    ("content", "kˈɑntɛnt", "noun", "", ""),
    # "Object to ..." opening a sentence is the verb ("ob-JECT"); misaki says the noun
    ("object", "əbʤˈɛkt", "verb", _SENTENCE_START, r"\s+to\b"),
    # close the verb ("kloze") after let's, please or a helping verb; misaki says "let's close" with the adjective
    ("close", "klˈOz", "verb",
     r"\b(?:let['’]s|please|will|would|can|could|should|must|might|may|to|(?:don|didn|won|can)['’]t)\s+", ""),
]


def heteronym_rules(rows: Iterable[tuple[str, str, str, str, str]] = HETERONYMS) -> dict[str, str]:
    """Turn ``(word, phonemes, name, before, after)`` rows into rules for :func:`apply_phonetic`, in row order."""
    rules = {}
    for word, ph, _name, before, after in rows:
        pattern = rf"(?im)(?P<pre>{before})(?<![\[\w-])(?P<w>{word})\b" + (rf"(?={after})" if after else "")
        rules[pattern] = rf"\g<pre>[\g<w>](/{ph}/)"
    return rules


KOKORO_FIXES.update(heteronym_rules())


# What each phoneme override above is called in the summary; one not listed shows as its phonemes.
_SOUND_NAMES = {"ˈɛpɑk": "EP-ock", "ˈɛpɑks": "EP-ocks","ɹˈid mˈi": "read me", "ɹˈid mˈiz": "read me",
                "ɹˈɛzəmˌA": "noun", "ɹˈɛzəmˌAz": "noun", "ɹəzˈum": "verb", "ɹəzˈumz": "verb", "lˈIv": "adjective",
                "ɹˈɛd": "past", "ɹˌiɹˈɛd": "past", "pɹˈufɹˌɛd": "past", "mɪsɹˈɛd": "past",
                "ɹˈid": "present", "ɹˌiɹˈid": "present", "pɹˈufɹˌid": "present", "mɪsɹˈid": "present",
                **{ph: name for _word, ph, name, _before, _after in HETERONYMS}}
_OVERRIDE = re.compile(r"\[([^\]]+)\]\(/([^/)]+)/\)")


def pronunciation_summary(texts: Iterable[str], *rule_sets: dict[str, str] | None) -> str:
    """One line naming each word a rule gave a sound to, and which, with counts; "" when no rule did.

    Runs the rules over ``texts`` the way :func:`apply_phonetic` does and counts the ``[word](/phonemes/)``
    overrides they write, e.g. ``"pronunciation: resume→noun ×3, read→past ×1"``. It makes a wrong guess
    visible before a video ships. Pass the rule sets in the order the voice applies them.
    """
    found: dict[str, int] = {}

    def count(m, repl):
        spoken = m.expand(repl)
        if not _OVERRIDE.search(m.group(0)):  # a word already rewritten was counted when it was
            for word, ph in _OVERRIDE.findall(spoken):
                label = f"{word.lower()}→{_SOUND_NAMES.get(ph, ph)}"
                found[label] = found.get(label, 0) + 1
        return spoken

    for text in texts:
        for rules in rule_sets:
            for pattern, repl in (rules or {}).items():
                text = re.sub(pattern, lambda m, repl=repl: count(m, repl), text)
    if not found:
        return ""
    listed = ", ".join(f"{label} ×{n}" for label, n in sorted(found.items(), key=lambda kv: (-kv[1], kv[0])))
    return "pronunciation: " + listed


class SilentTTS:
    """Emit a silent WAV of a fixed length. Pure stdlib, fully deterministic."""

    def __init__(self, seconds: float = 3.0, sample_rate: int = 24000):
        if seconds <= 0:
            raise ValueError("seconds must be > 0")
        self.seconds = float(seconds)
        self.sample_rate = int(sample_rate)

    def synthesize(self, text: str, out_path: Path) -> float:
        n_frames = int(self.seconds * self.sample_rate)
        with wave.open(str(out_path), "w") as w:
            w.setnchannels(1)
            w.setsampwidth(2)  # 16-bit
            w.setframerate(self.sample_rate)
            w.writeframes(struct.pack("<h", 0) * n_frames)
        return self.seconds


class KokoroTTS:
    """Client for any OpenAI-compatible ``/v1/audio/speech`` endpoint.

    Defaults to WAV so the clip duration is measurable (lets the reel pad each
    segment to fit the speech exactly). Set ``response_format="mp3"`` for smaller
    files; duration then reads as unknown and the segment uses ``-shortest``.
    """

    def __init__(
        self,
        url: str = "http://127.0.0.1:8021/v1/audio/speech",
        voice: str = "af_heart",
        model: str = "kokoro",
        response_format: str = "wav",
        timeout: float = 180.0,
        phonetic: dict[str, str] | None = None,
        session=None,
        acronym_plurals: bool = True,
        pronunciations: dict[str, str] | None = None,
    ):
        self.url = url
        self.voice = voice
        self.model = model
        self.response_format = response_format
        self.timeout = timeout
        self.phonetic = phonetic
        self.acronym_plurals = acronym_plurals
        self.pronunciations = pronunciations
        self._session = session

    def synthesize(self, text: str, out_path: Path) -> float | None:
        import requests

        spoken = _pronounce(text, self.phonetic, self.acronym_plurals, self.pronunciations)
        poster = self._session or requests
        resp = poster.post(
            self.url,
            json={
                "model": self.model,
                "voice": self.voice,
                "input": spoken,
                "response_format": self.response_format,
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        Path(out_path).write_bytes(resp.content)
        return wav_duration(out_path) if self.response_format == "wav" else None


# Homebrew's espeak-ng, which misaki falls back to for out-of-dictionary words.
# The espeakng-loader wheel's own copy points at its build machine's data path,
# so phonemizing fails unless espeak is pointed at a real install.
HOMEBREW_ESPEAK = {
    "ESPEAK_DATA_PATH": "/opt/homebrew/share/espeak-ng-data",
    "PHONEMIZER_ESPEAK_LIBRARY": "/opt/homebrew/lib/libespeak-ng.dylib",
}


def use_homebrew_espeak(env=None, exists: Callable[[str], bool] = os.path.exists) -> dict[str, str]:
    """Point espeak at Homebrew's ``espeak-ng`` when it's installed.

    Sets each variable in :data:`HOMEBREW_ESPEAK` that is unset in ``env``
    (default ``os.environ``) and whose path exists, and returns those it set.
    Anything already set is left alone.
    """
    env = os.environ if env is None else env
    applied = {k: v for k, v in HOMEBREW_ESPEAK.items() if not env.get(k) and exists(v)}
    env.update(applied)
    return applied


def phonemes(text: str, fixes: dict[str, str] | None = KOKORO_FIXES) -> tuple[str, str, list[tuple[str, str]]]:
    """What Kokoro would be told to say for ``text``: the text after ``fixes``, its phonemes, and each word's.

    Runs misaki's English G2P (the one Kokoro uses) over the fixed text, so a wrong sound shows before a video is
    built. Pass ``fixes=None`` for the sound with no rule. Returns ``(spoken, phonemes, [(word, phonemes), ...])``.
    """
    use_homebrew_espeak()
    import misaki.en
    spoken = apply_phonetic(text, fixes)
    ps, tokens = misaki.en.G2P(trf=False, british=False)(spoken)
    return spoken, ps, [(t.text, t.phonemes or "") for t in tokens]


def _load_mlx_model(model: str):
    use_homebrew_espeak()
    try:
        import misaki.en  # noqa: F401 — Kokoro's English G2P; fail now, not mid-reel
        from mlx_audio.tts.utils import load_model
    except ImportError as e:
        raise ImportError(
            "MLXKokoroTTS needs mlx-audio and misaki (Apple Silicon only): "
            "pip install 'slidecast[mlx]'"
        ) from e
    return load_model(model_path=model)


def _write_wav(path: Path, chunks, sample_rate: int) -> float:
    """Write float audio chunks (-1..1) as a 16-bit mono WAV; return its length."""
    pcm = array("h")
    for chunk in chunks:
        samples = chunk.tolist() if hasattr(chunk, "tolist") else chunk
        pcm.extend(int(max(-1.0, min(1.0, x)) * 32767) for x in samples)
    with wave.open(str(path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm.tobytes())
    return len(pcm) / float(sample_rate)


# Where Kokoro's pipeline splits text before phonemizing: lines and sentence ends. It splits long text again at
# 510 phonemes, but its own count can come out one over and the end gets truncated ("len(ps) == 511 > 510");
# a sentence at a time never gets near the limit.
SENTENCES = r"\n+|(?<=[.!?…])\s+|(?<=[.!?…][\"”’])\s+"


class MLXKokoroTTS:
    """Kokoro run in-process on Apple Silicon via ``mlx-audio`` — no TTS server.

    Install with ``pip install 'slidecast[mlx]'``. The model loads on the first
    line spoken and is reused for the rest of the reel. Writes a 24 kHz WAV, so
    the duration is always known. ``lang_code`` is Kokoro's: "a" American
    English, "b" British. If Homebrew's ``espeak-ng`` is installed it is used for
    out-of-dictionary words (see :func:`use_homebrew_espeak`).

    ``fixes`` (default :data:`KOKORO_FIXES`) are rules like ``phonetic`` that
    every reel needs, applied after it; pass ``None`` to turn them off.

    ``acronym_plurals`` (default on) joins a spelled-out plural acronym back
    into one word ("P D Fs" to "PDFs", see :func:`normalize_acronym_plurals`),
    which Kokoro says right. ``pronunciations`` is a ``{"SOCs": "socks"}``
    dictionary of whole words and what to say instead (see
    :func:`apply_pronunciations`). Both change the speech only, never the
    on-screen text; they run after ``phonetic`` and before ``fixes``.

    ``split_pattern`` (default :data:`SENTENCES`) is where the text is cut
    before Kokoro phonemizes it.

    ``loader`` maps a model id to a loaded model (default ``mlx_audio``'s
    ``load_model``); tests swap in a stub.
    """

    def __init__(
        self,
        voice: str = "af_heart",
        model: str = "mlx-community/Kokoro-82M-bf16",
        speed: float = 1.0,
        lang_code: str = "a",
        phonetic: dict[str, str] | None = None,
        loader: Callable[[str], object] | None = None,
        fixes: dict[str, str] | None = KOKORO_FIXES,
        split_pattern: str = SENTENCES,
        acronym_plurals: bool = True,
        pronunciations: dict[str, str] | None = None,
    ):
        self.voice = voice
        self.model = model
        self.speed = float(speed)
        self.lang_code = lang_code
        self.phonetic = phonetic
        self.acronym_plurals = acronym_plurals
        self.pronunciations = pronunciations
        self.fixes = fixes
        self.split_pattern = split_pattern
        self._loader = loader or _load_mlx_model
        self._model = None

    def synthesize(self, text: str, out_path: Path) -> float:
        if self._model is None:
            self._model = self._loader(self.model)
        chunks, rate = [], 24000
        for result in self._model.generate(
            apply_phonetic(_pronounce(text, self.phonetic, self.acronym_plurals, self.pronunciations), self.fixes),
            voice=self.voice,
            speed=self.speed, lang_code=self.lang_code, split_pattern=self.split_pattern,
        ):
            chunks.append(result.audio)
            rate = result.sample_rate
        if not chunks:
            raise RuntimeError(f"Kokoro produced no audio for {text[:40]!r}")
        return _write_wav(Path(out_path), chunks, rate)


class GTTSTTS:
    """Google Translate TTS via the ``gtts`` package. Emits MP3 (duration unknown)."""

    def __init__(self, lang: str = "en", tld: str = "com", slow: bool = False,
                 phonetic: dict[str, str] | None = None):
        self.lang = lang
        self.tld = tld
        self.slow = slow
        self.phonetic = phonetic

    def synthesize(self, text: str, out_path: Path) -> float | None:
        from gtts import gTTS

        spoken = apply_phonetic(text, self.phonetic)
        gTTS(text=spoken, lang=self.lang, tld=self.tld, slow=self.slow).save(str(out_path))
        return None


class MacSayTTS:
    """macOS's built-in speech synthesizer (``say``). Offline, no dependencies, WAV.

    ``voice`` is any name from ``say -v '?'``. The standard voices are serviceable.
    Premium ones such as "Ava (Premium)" or "Zoe (Premium)" sound far better and
    are a free download under System Settings › Accessibility › Spoken Content.
    ``rate`` is words per minute (``say`` defaults to about 175).
    """

    def __init__(self, voice: str = "Samantha", rate: int | None = None,
                 sample_rate: int = 24000, phonetic: dict[str, str] | None = None,
                 runner=None):
        self.voice = voice
        self.rate = rate
        self.sample_rate = int(sample_rate)
        self.phonetic = phonetic
        self.runner = runner

    def synthesize(self, text: str, out_path: Path) -> float | None:
        import subprocess

        cmd = ["say", "-v", self.voice, "-o", str(out_path),
               "--file-format=WAVE", f"--data-format=LEI16@{self.sample_rate}"]
        if self.rate:
            cmd += ["-r", str(self.rate)]
        # Text goes in on stdin, so nothing in it can be read as an option.
        (self.runner or subprocess.run)(cmd, input=apply_phonetic(text, self.phonetic),
                                        text=True, check=True)
        return wav_duration(out_path)
