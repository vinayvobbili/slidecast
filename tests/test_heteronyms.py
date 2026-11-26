"""Heteronyms: one test per rule in tts.HETERONYMS and per sentence cue for "read", a snapshot over a paragraph, and
pins on what misaki already says right on its own (so a row is added only where it is wrong)."""

import pytest

from slidecast import apply_phonetic
from slidecast.tts import HETERONYMS, KOKORO_FIXES, heteronym_rules, pronunciation_summary

RED, REED = "/ɹˈɛd/", "/ɹˈid/"


@pytest.mark.parametrize("line, said", [
    # another past verb in the sentence makes "I read" the past
    ("I read the logs and found the bug.", f"I [read]({RED}) the logs and found the bug."),
    ("I read it and decided to escalate.", f"I [read]({RED}) it and decided to escalate."),
    ("We read the runbook, which showed the gap.", f"We [read]({RED}) the runbook, which showed the gap."),
    ("When the alert fired, I read the logs.", f"When the alert fired, I [read]({RED}) the logs."),
    ("After Globex called, we read the contract.", f"After Globex called, we [read]({RED}) the contract."),
    # a habit makes it the present, even with a past verb in the sentence
    ("I read logs every morning.", f"I [read]({REED}) logs every morning."),
    ("Every morning I read the logs.", f"Every morning I [read]({REED}) the logs."),
    ("I read the automated reports daily.", f"I [read]({REED}) the automated reports daily."),
    ("I usually read it first and filed nothing.", f"I usually [read]({REED}) it first and filed nothing."),
    # no cue in the sentence: "I read" stays the present, a helping verb wins over a past verb later on
    ("I read the logs.", f"I [read]({REED}) the logs."),
    ("I want to read it and asked for access.", f"I want to [read]({REED}) it and asked for access."),
    ("Initech staff can read logs and filed tickets.", f"Initech staff can [read]({REED}) logs and filed tickets."),
    # "need", "speed", "hundred" are not past verbs
    ("I read a hundred pages at speed.", f"I [read]({REED}) a hundred pages at speed."),
])
def test_read_takes_its_tense_from_the_rest_of_the_sentence(line, said):
    assert apply_phonetic(line, KOKORO_FIXES) == said


@pytest.mark.parametrize("line, said", [
    ("A lead pipe and lead paint.", "A [lead](/lˈɛd/) pipe and [lead](/lˈɛd/) paint."),
    ("Lead poisoning, lead-free solder.", "[Lead](/lˈɛd/) poisoning, [lead](/lˈɛd/) free solder."),
    ("Lead and copper.", "[Lead](/lˈɛd/) and copper."),
    ("The keel is made of lead.", "The keel is made of [lead](/lˈɛd/)."),
    ("Heavy as lead, it sank.", "Heavy as [lead](/lˈɛd/), it sank."),
])
def test_lead_the_metal_is_led(line, said):
    assert apply_phonetic(line, KOKORO_FIXES) == said


@pytest.mark.parametrize("line", [
    "I lead the team.", "The lead engineer led it.", "She served as lead.", "Lead time is short.",
    "I lead and mentor two people.", "Our tech lead.", "Made of lead generation tools.",
])
def test_lead_the_verb_or_role_is_left_to_misaki(line):
    assert apply_phonetic(line, KOKORO_FIXES) == line


@pytest.mark.parametrize("line, said", [
    ("A tear in the fabric.", "A [tear](/tˈɛɹ/) in the fabric."),
    ("Two tears in the canvas.", "Two [tears](/tˈɛɹz/) in the canvas."),
    ("Tears in her eyes.", "Tears in her eyes."),
    ("A tear in the eye.", "A tear in the eye."),
    ("A tear rolled down.", "A tear rolled down."),
])
def test_tear_the_rip(line, said):
    assert apply_phonetic(line, KOKORO_FIXES) == said


@pytest.mark.parametrize("line, said", [
    ("Turn up the bass.", "Turn up the [bass](/bˈAs/)."),
    ("A bass line and a bass guitar.", "A [bass](/bˈAs/) line and a [bass](/bˈAs/) guitar."),
    ("Striped bass.", "Striped [bass](/bˈæs/)."),
    ("We caught a bass.", "We caught a [bass](/bˈæs/)."),
    ("Bass fishing on the lake.", "[Bass](/bˈæs/) fishing on the lake."),
])
def test_bass_is_the_sound_unless_its_the_fish(line, said):
    assert apply_phonetic(line, KOKORO_FIXES) == said


@pytest.mark.parametrize("line, said", [
    ("Minute changes add up.", "[Minute](/mInˈut/) changes add up."),
    ("A minute difference in timing.", "A [minute](/mInˈut/) difference in timing."),
    ("At the last minute changes happen.", "At the last minute changes happen."),
    ("Wait a minute.", "Wait a minute."),
    ("A ten minute call.", "A ten minute call."),
])
def test_minute_as_in_tiny_before_what_it_describes(line, said):
    assert apply_phonetic(line, KOKORO_FIXES) == said


@pytest.mark.parametrize("line, said", [
    ("Content moderation at Initech.", "[Content](/kˈɑntɛnt/) moderation at Initech."),
    ("What is content moderation?", "What is [content](/kˈɑntɛnt/) moderation?"),
    ("The content is good.", "The [content](/kˈɑntɛnt/) is good."),
    ("He seemed content.", "He seemed [content](/kəntˈɛnt/)."),
    ("I am content with it.", "I am [content](/kəntˈɛnt/) with it."),
    ("We're content to wait.", "We're [content](/kəntˈɛnt/) to wait."),
    ("Am I content?", "Am I [content](/kəntˈɛnt/)?"),
])
def test_content_is_the_noun_unless_a_linking_word_makes_it_the_adjective(line, said):
    assert apply_phonetic(line, KOKORO_FIXES) == said


@pytest.mark.parametrize("line, said", [
    ("Object to the plan.", "[Object](/əbʤˈɛkt/) to the plan."),
    ("Fine. Object to it later.", "Fine. [Object](/əbʤˈɛkt/) to it later."),
    ("The object model.", "The object model."),
    ("The object to the left.", "The object to the left."),
])
def test_object_to_opening_a_sentence_is_the_verb(line, said):
    assert apply_phonetic(line, KOKORO_FIXES) == said


@pytest.mark.parametrize("line, said", [
    ("Let's close.", "Let's [close](/klˈOz/)."),
    ("Please close the ticket.", "Please [close](/klˈOz/) the ticket."),
    ("We will close by Friday.", "We will [close](/klˈOz/) by Friday."),
    ("Is it close to done?", "Is it close to done?"),
    ("A close call.", "A close call."),
])
def test_close_after_a_helping_word_is_the_verb(line, said):
    assert apply_phonetic(line, KOKORO_FIXES) == said


def test_a_paragraph_snapshot():
    paragraph = ("I lead the platform team at Initech. When the alert fired, I read the logs and found a lead pipe "
                 "in the asset list. I read logs every morning, and the live demo went fine. Let's close the ticket; "
                 "the content is good. Upload your resume. We live in Boulder.")
    assert apply_phonetic(paragraph, KOKORO_FIXES) == (
        "I lead the platform team at Initech. When the alert fired, I [read](/ɹˈɛd/) the logs and found a "
        "[lead](/lˈɛd/) pipe in the asset list. I [read](/ɹˈid/) logs every morning, and the [live](/lˈIv/) demo "
        "went fine. Let's [close](/klˈOz/) the ticket; the [content](/kˈɑntɛnt/) is good. Upload your "
        "[resume](/ɹˈɛzəmˌA/). We live in Boulder.")


def test_a_row_is_easy_to_add():
    rules = heteronym_rules([("wound", "wˈund", "injury", r"\b(?:a|the)\s+", "")])
    assert apply_phonetic("The wound healed. It wound down.", rules) == "The [wound](/wˈund/) healed. It wound down."


def test_every_row_has_a_name_and_phonemes():
    for word, ph, name, _before, _after in HETERONYMS:
        assert word and ph and name and "/" not in ph


def test_pronunciation_summary_names_the_heteronym_sounds():
    assert pronunciation_summary(["A lead pipe. Turn up the bass. The content is good."], KOKORO_FIXES) == (
        "pronunciation: bass→music ×1, content→noun ×1, lead→metal ×1")


@pytest.mark.parametrize("line, word, sound", [
    ("I lead the team.", "lead", "lˈid"),
    ("The lead engineer led it.", "lead", "lˈid"),
    ("The lead engineer led it.", "led", "lˈɛd"),
    ("I use it daily.", "use", "jˈuz"),
    ("The use case is clear.", "use", "jˈus"),
    ("We live in Boulder.", "live", "lˈɪv"),
    ("Record the call.", "Record", "ɹəkˈɔɹd"),
    ("A track record.", "record", "ɹˈɛkəɹd"),
    ("Present the slides.", "Present", "pɹizˈɛnt"),
    ("Everyone was present.", "present", "pɹˈɛzᵊnt"),
    ("The wind is strong.", "wind", "wˈɪnd"),
    ("Wind the cable.", "Wind", "wˈInd"),
    ("Close the loop.", "Close", "klˈOz"),
    ("A close call.", "close", "klˈOs"),
    ("Wait a minute.", "minute", "mˈɪnət"),
    ("He read the notes.", "read", "ɹˈɛd"),
])
def test_misaki_gets_these_right_without_a_row(line, word, sound):
    pytest.importorskip("misaki.en")
    from slidecast.tts import phonemes

    assert (word, sound) in phonemes(line)[2]


@pytest.mark.parametrize("line, word, sound", [
    ("Lead-based paint.", "Lead", "lˈɛd"),
    ("A tear in the fabric.", "tear", "tˈɛɹ"),
    ("Turn up the bass.", "bass", "bˈAs"),
    ("Content moderation.", "Content", "kˈɑntɛnt"),
    ("I read the logs and found it.", "read", "ɹˈɛd"),
])
def test_misaki_takes_each_rows_phonemes(line, word, sound):
    pytest.importorskip("misaki.en")
    from slidecast.tts import phonemes

    assert (word, sound) in phonemes(line)[2]


@pytest.mark.parametrize("line, said", [
    # "read-only" is the present "read", then "only", with no hyphen to pause at (any case, any punctuation after it)
    ("The tools are read-only.", f"The tools are [read]({REED}) only."),
    ("Read-Only access.", f"[Read]({REED}) Only access."),
    ("It is read-only, by design.", f"It is [read]({REED}) only, by design."),
    ("A read-only copy and read-write access.", f"A [read]({REED}) only copy and [read]({REED}) write access."),
    # any other hyphen between letters is a space too, whatever rule ran first ("lead-free" keeps its metal sound)
    ("A human-in-the-loop, least-privilege, hard-coded setup.",
     "A human in the loop, least privilege, hard coded setup."),
    ("Lead-free solder.", "[Lead](/lˈɛd/) free solder."),
    # digits, spaced dashes and double dashes are left alone
    ("The W-2 form in mid-2026, a 90-second answer - or -- not.",
     "The W-2 form in mid-2026, a 90-second answer - or -- not."),
])
def test_a_hyphen_between_letters_is_said_as_a_space_so_the_voice_does_not_pause(line, said):
    assert apply_phonetic(line, KOKORO_FIXES) == said


def test_read_only_comes_out_of_misaki_with_no_dash_between_the_words():
    pytest.importorskip("misaki.en")
    from slidecast.tts import phonemes

    for line in ("The tools are read-only for everyone.", "Everything is Read-Only, then logged."):
        _spoken, ps, _words = phonemes(line)
        assert "—" not in ps and "ɹˈid ˈOnli" in ps, line
    assert "—" in phonemes("The tools are read-only for everyone.", fixes=None)[1]  # without the rule: the dash
