import wave

from slidecast import SilentTTS, apply_phonetic, wav_duration
from slidecast.tts import KokoroTTS


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
