import shutil
import struct
import wave

import pytest

from slidecast import SilentTTS, apply_phonetic, wav_duration
from slidecast.tts import SENTENCES, KokoroTTS


def test_silent_tts_writes_valid_wav_of_expected_length(tmp_path):
    out = tmp_path / "s.wav"
    dur = SilentTTS(seconds=2.0, sample_rate=8000).synthesize("ignored", out)
    assert dur == 2.0
    with wave.open(str(out)) as w:
        assert w.getframerate() == 8000
        assert w.getnframes() == 16000
    assert abs(wav_duration(out) - 2.0) < 1e-6


def test_wav_duration_none_for_non_wav(tmp_path):
    p = tmp_path / "not.wav"
    p.write_bytes(b"not a wav")
    assert wav_duration(p) is None


def test_apply_phonetic_rewrites_only_matches():
    rules = {r"\bSOC\b": "sock", r"\bSOCs\b": "socks"}
    assert apply_phonetic("the SOC team", rules) == "the sock team"
    assert apply_phonetic("no acronyms here", rules) == "no acronyms here"
    assert apply_phonetic("text", None) == "text"


def test_kokoro_measures_wav_duration_with_injected_session(tmp_path):
    # A real WAV payload so KokoroTTS can measure it without a network call.
    wav_path = tmp_path / "payload.wav"
    SilentTTS(seconds=1.5, sample_rate=16000).synthesize("", wav_path)
    payload = wav_path.read_bytes()

    class FakeResp:
        content = payload

        def raise_for_status(self):
            pass

    class FakeSession:
        def __init__(self):
            self.posted = None

        def post(self, url, json, timeout):
            self.posted = {"url": url, "json": json, "timeout": timeout}
            return FakeResp()

    sess = FakeSession()
    tts = KokoroTTS(voice="af_sky", phonetic={r"\bSOC\b": "sock"}, session=sess)
    out = tmp_path / "out.wav"
    dur = tts.synthesize("the SOC desk", out)

    assert abs(dur - 1.5) < 1e-6
    assert out.read_bytes() == payload
    # phonetic rewrite reached the request body
    assert sess.posted["json"]["input"] == "the sock desk"
    assert sess.posted["json"]["voice"] == "af_sky"


def test_kokoro_mp3_reports_unknown_duration(tmp_path):
    class FakeResp:
        content = b"ID3fake-mp3-bytes"

        def raise_for_status(self):
            pass

    class FakeSession:
        def post(self, *a, **k):
            return FakeResp()

    tts = KokoroTTS(response_format="mp3", session=FakeSession())
    out = tmp_path / "out.mp3"
    assert tts.synthesize("hello", out) is None
    assert out.read_bytes() == b"ID3fake-mp3-bytes"


def test_mac_say_passes_text_on_stdin_and_measures_the_wav(tmp_path):
    from slidecast import MacSayTTS

    calls = []

    def runner(cmd, **kwargs):
        calls.append((cmd, kwargs))
        SilentTTS(seconds=1.5).synthesize("", cmd[cmd.index("-o") + 1])

    tts = MacSayTTS(voice="Ava (Premium)", rate=180, phonetic={r"\bSOC\b": "sock"}, runner=runner)
    assert tts.synthesize("-v is not a flag; SOC 2", tmp_path / "a.wav") == pytest.approx(1.5)
    cmd, kwargs = calls[0]
    assert cmd[:3] == ["say", "-v", "Ava (Premium)"]
    assert "--data-format=LEI16@24000" in cmd and cmd[cmd.index("-r") + 1] == "180"
    assert kwargs["input"] == "-v is not a flag; sock 2"


@pytest.mark.skipif(shutil.which("say") is None, reason="macOS `say` not available")
def test_mac_say_really_speaks(tmp_path):
    from slidecast import MacSayTTS

    seconds = MacSayTTS().synthesize("Hello.", tmp_path / "hello.wav")
    assert seconds and 0.2 < seconds < 5


class _Result:
    def __init__(self, audio, sample_rate=24000):
        self.audio, self.sample_rate = audio, sample_rate


class _FakeKokoro:
    """Stands in for an mlx-audio Kokoro model: yields two chunks of samples."""

    def __init__(self):
        self.calls = []

    def generate(self, text, **kwargs):
        self.calls.append((text, kwargs))
        yield _Result([0.0, 0.5, -0.5, 2.0] * 6000)  # 1.0 s, with a sample to clip
        yield _Result([0.25] * 12000)  # 0.5 s


def test_mlx_kokoro_loads_once_and_writes_a_measured_wav(tmp_path):
    from slidecast import MLXKokoroTTS

    model, loads = _FakeKokoro(), []
    tts = MLXKokoroTTS(voice="am_adam", speed=1.2, phonetic={r"\bSOC\b": "sock"},
                       loader=lambda name: loads.append(name) or model)
    assert loads == []  # lazy: no model until there's something to say

    assert tts.synthesize("the SOC desk", tmp_path / "a.wav") == pytest.approx(1.5)
    assert tts.synthesize("again", tmp_path / "b.wav") == pytest.approx(1.5)
    assert loads == ["mlx-community/Kokoro-82M-bf16"]
    text, kwargs = model.calls[0]
    assert text == "the sock desk"
    assert kwargs == {"voice": "am_adam", "speed": 1.2, "lang_code": "a", "split_pattern": SENTENCES}
    with wave.open(str(tmp_path / "a.wav")) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 24000)
        frames = w.readframes(4)
    assert struct.unpack("<4h", frames) == (0, 16383, -16383, 32767)  # 2.0 clipped


@pytest.mark.parametrize("line, said", [
    ("We went live in production.", "We went [live](/lˈIv/) in production."),
    ("Going live is a toggle.", "Going [live](/lˈIv/) is a toggle."),
    ("I ran it live on a copy.", "I ran it [live](/lˈIv/) on a copy."),
    ("The live runs used 2 min.", "The [live](/lˈIv/) runs used 2 min."),
    ("Is it live now?", "Is it live now?"),  # "it live" alone isn't a pattern: left to the tagger
    ("I live in North Carolina.", "I live in North Carolina."),
    ("An assistant that lives in Webex.", "An assistant that lives in Webex."),
])
def test_kokoro_fixes_give_live_the_adjectives_sound_only_where_its_one(line, said):
    from slidecast.tts import KOKORO_FIXES

    assert apply_phonetic(line, KOKORO_FIXES) == said


@pytest.mark.parametrize("line, said", [
    ("I switched to JSON mode.", "I switched to Jason mode."),
    ("It salvages the json from a chatty reply.", "It salvages the Jason from a chatty reply."),
    ("JSONPath and JSONL stay as they are.", "JSONPath and JSONL stay as they are."),
    ("Thirteen packages on PyPI.", "Thirteen packages on pie pee eye."),
])
def test_kokoro_fixes_say_names_as_words(line, said):
    from slidecast.tts import KOKORO_FIXES

    assert apply_phonetic(line, KOKORO_FIXES) == said


def test_kokoro_is_given_one_sentence_at_a_time():
    import re

    text = 'It shipped. Did it help? Yes! He said "no." Then 2.5x faster…\nNew line: e.g. this too'
    assert [s for s in re.split(SENTENCES, text) if s] == [
        "It shipped.", "Did it help?", "Yes!", 'He said "no."', "Then 2.5x faster…", "New line: e.g.", "this too"]


def test_mlx_kokoro_applies_the_fixes_after_the_callers_rules(tmp_path):
    from slidecast import MLXKokoroTTS

    model = _FakeKokoro()
    MLXKokoroTTS(phonetic={r"\bSOC\b": "sock"}, loader=lambda name: model).synthesize(
        "The SOC went live.", tmp_path / "a.wav")
    MLXKokoroTTS(fixes=None, loader=lambda name: model).synthesize("We went live.", tmp_path / "b.wav")
    assert [text for text, _ in model.calls] == ["The sock went [live](/lˈIv/).", "We went live."]


def test_misaki_takes_the_adjectives_sound_from_the_fix():
    en = pytest.importorskip("misaki.en")
    from slidecast.tts import KOKORO_FIXES

    phonemes, _ = en.G2P(trf=False, british=False)(apply_phonetic("We went live in production.", KOKORO_FIXES))
    assert "lˈIv" in phonemes and "lˈɪv" not in phonemes


def test_mlx_kokoro_explains_how_to_install_it(monkeypatch, tmp_path):
    import builtins

    from slidecast import MLXKokoroTTS

    real_import = builtins.__import__

    def no_mlx(name, *args, **kwargs):
        if name.startswith(("mlx_audio", "misaki")):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_mlx)
    with pytest.raises(ImportError, match=r"pip install 'slidecast\[mlx\]'"):
        MLXKokoroTTS().synthesize("hello", tmp_path / "a.wav")


def test_homebrew_espeak_fills_only_unset_vars_whose_paths_exist():
    from slidecast.tts import HOMEBREW_ESPEAK, use_homebrew_espeak

    env = {"PHONEMIZER_ESPEAK_LIBRARY": "/my/libespeak.dylib"}
    applied = use_homebrew_espeak(env, exists=lambda p: True)
    assert applied == {"ESPEAK_DATA_PATH": HOMEBREW_ESPEAK["ESPEAK_DATA_PATH"]}
    assert env["PHONEMIZER_ESPEAK_LIBRARY"] == "/my/libespeak.dylib"

    env = {}
    assert use_homebrew_espeak(env, exists=lambda p: False) == {} and env == {}


def _has_mlx_audio():
    try:
        import mlx_audio  # noqa: F401
    except Exception:  # noqa: BLE001 — absent, or present but unusable here
        return False
    return True


@pytest.mark.skipif(not _has_mlx_audio(), reason="mlx-audio not installed")
def test_mlx_kokoro_really_speaks(tmp_path):
    from slidecast import MLXKokoroTTS

    tts = MLXKokoroTTS()
    seconds = tts.synthesize("Hello from slidecast. The SOC team says hi.", tmp_path / "a.wav")
    assert 1.0 < seconds < 8
    assert wav_duration(tmp_path / "a.wav") == pytest.approx(seconds)
    assert tts.synthesize("Second line.", tmp_path / "b.wav") > 0.3  # model reused
