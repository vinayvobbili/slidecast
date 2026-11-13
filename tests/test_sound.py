import wave

import pytest

from slidecast import sound

np = pytest.importorskip("numpy")


def test_bed_is_as_long_as_asked_and_stereo():
    bed = sound.music_bed(4.0)
    assert bed.shape == (4 * sound.SAMPLE_RATE, 2)
    assert np.isfinite(bed).all() and np.abs(bed).max() > 0


def test_bed_fades_in_and_out():
    bed = sound.music_bed(6.0)
    assert np.abs(bed[0]).max() == 0  # starts from silence
    assert np.abs(bed[-1]).max() < 1e-3  # and fades to it
    middle = bed[3 * sound.SAMPLE_RATE: 4 * sound.SAMPLE_RATE]
    assert np.abs(middle).max() > 50 * np.abs(bed[-100:]).max()


def test_the_same_seed_gives_the_same_bed():
    assert np.array_equal(sound.music_bed(3.0, seed=1), sound.music_bed(3.0, seed=1))
    assert not np.array_equal(sound.music_bed(3.0, seed=1), sound.music_bed(3.0, seed=2))
    assert np.array_equal(sound.intro_sting(seed=5), sound.intro_sting(seed=5))


def test_tempo_changes_the_bed():
    assert not np.array_equal(sound.music_bed(3.0, bpm=84), sound.music_bed(3.0, bpm=100))


def test_bed_rejects_a_zero_length_or_tempo():
    with pytest.raises(ValueError):
        sound.music_bed(0)
    with pytest.raises(ValueError):
        sound.music_bed(3.0, bpm=0)


def test_sting_lengths():
    assert sound.intro_sting().shape == pytest.approx((5.1 * sound.SAMPLE_RATE, 2), abs=1)
    assert sound.outro_sting().shape == (5 * sound.SAMPLE_RATE, 2)


def test_write_wav_is_16_bit_stereo_normalized_to_the_peak(tmp_path):
    x = np.zeros((sound.SAMPLE_RATE // 2, 2))
    x[100] = [4.0, -2.0]  # far over full scale before normalizing
    seconds = sound.write_wav(tmp_path / "x.wav", x, peak=0.5)
    assert seconds == pytest.approx(0.5)
    with wave.open(str(tmp_path / "x.wav")) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (2, 2, sound.SAMPLE_RATE)
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").reshape(-1, 2)
    assert pcm[100].tolist() == [int(0.5 * 32767), int(-0.25 * 32767)]


def test_write_wav_doubles_mono_and_survives_silence(tmp_path):
    sound.write_wav(tmp_path / "quiet.wav", np.zeros(441))
    with wave.open(str(tmp_path / "quiet.wav")) as w:
        assert w.getnchannels() == 2 and w.getnframes() == 441


def test_compose_writes_the_three_files(tmp_path):
    from slidecast.tts import wav_duration

    paths = sound.compose(tmp_path / "audio", length=3.0, bpm=90, seed=3)
    assert sorted(p.name for p in paths.values()) == [
        "intro_sting.wav", "music_bed.wav", "outro_sting.wav"]
    assert wav_duration(paths["music_bed"]) == pytest.approx(3.0)
    assert wav_duration(paths["intro_sting"]) == pytest.approx(5.1, abs=0.01)
    assert wav_duration(paths["outro_sting"]) == pytest.approx(5.0)


def _read_bed(path):
    with wave.open(str(path)) as w:
        assert w.getnchannels() == 2 and w.getframerate() == sound.SAMPLE_RATE
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").reshape(-1, 2) / 32767


def test_a_short_bed_is_written_whole(tmp_path):
    seconds = sound.write_long_bed(tmp_path / "bed.wav", 3.0)
    assert seconds == pytest.approx(3.0)
    assert len(_read_bed(tmp_path / "bed.wav")) == 3 * sound.SAMPLE_RATE


def test_a_long_bed_loops_and_still_fades_at_both_ends(tmp_path, monkeypatch):
    monkeypatch.setattr(sound, "LOOP_ABOVE", 10.0)
    seconds = sound.write_long_bed(tmp_path / "bed.wav", 200.0, peak=0.8)
    assert 200.0 <= seconds < 200.0 + 40.0
    bed = _read_bed(tmp_path / "bed.wav")
    assert len(bed) / sound.SAMPLE_RATE == pytest.approx(seconds)
    assert np.abs(bed).max() == pytest.approx(0.8, abs=0.01)
    assert np.abs(bed[0]).max() < 1e-3 and np.abs(bed[-1]).max() < 1e-3
    middle = bed[100 * sound.SAMPLE_RATE: 101 * sound.SAMPLE_RATE]
    assert np.abs(middle).max() > 0.1
    # the seams between repeats are crossfaded: no click beyond the kick drum's own attack
    assert np.abs(np.diff(bed, axis=0)).max() < 0.3


def test_a_long_bed_needs_the_same_memory_however_long(tmp_path, monkeypatch):
    import tracemalloc

    monkeypatch.setattr(sound, "LOOP_ABOVE", 10.0)

    def peak(length):
        tracemalloc.start()
        sound.write_long_bed(tmp_path / "bed.wav", length)
        _, high = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        return high

    assert peak(600.0) < 1.2 * peak(150.0)


def test_missing_numpy_says_which_extra_to_install(monkeypatch):
    monkeypatch.setattr(sound, "np", None)
    with pytest.raises(ImportError, match=r"pip install 'slidecast\[compose\]'"):
        sound.music_bed(3.0)
    with pytest.raises(ImportError, match="needs numpy"):
        sound.compose("unused")


# --- the upbeat style ---------------------------------------------------------------------------------------

def _db(x):
    return 20 * np.log10(np.sqrt(np.mean(np.square(x))))


def _band_db(x, lo, hi):
    """Level (dB re full scale) of the part of mono ``x`` between ``lo`` and ``hi`` Hz."""
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / sound.SAMPLE_RATE)
    spec[(f < lo) | (f >= hi)] = 0
    return _db(np.fft.irfft(spec, len(x)))


def test_style_selection_changes_the_audio_and_the_default_tempo():
    calm, upbeat = sound.music_bed(4.0), sound.music_bed(4.0, style="upbeat")
    assert calm.shape == upbeat.shape and not np.array_equal(calm, upbeat)
    assert np.array_equal(calm, sound.music_bed(4.0, style="calm"))
    assert sound.default_bpm("calm") == 84 and 118 <= sound.default_bpm("upbeat") <= 128
    assert not np.array_equal(sound.intro_sting(), sound.intro_sting(style="upbeat"))
    assert not np.array_equal(sound.outro_sting(), sound.outro_sting(style="upbeat"))
    assert sound.intro_sting(style="upbeat").shape == sound.intro_sting().shape
    assert sound.outro_sting(style="upbeat").shape == sound.outro_sting().shape


def test_an_unknown_style_is_refused():
    with pytest.raises(ValueError, match="unknown style"):
        sound.music_bed(3.0, style="polka")
    with pytest.raises(ValueError, match="unknown style"):
        sound.intro_sting(style="polka")


def test_the_upbeat_bed_is_deterministic_and_seeded():
    a = sound.music_bed(6.0, style="upbeat", seed=3)
    assert np.array_equal(a, sound.music_bed(6.0, style="upbeat", seed=3))
    assert not np.array_equal(a, sound.music_bed(6.0, style="upbeat", seed=4))
    assert np.array_equal(sound.intro_sting(seed=2, style="upbeat"), sound.intro_sting(seed=2, style="upbeat"))


def test_the_upbeat_bed_has_a_four_on_the_floor_kick():
    bpm = sound.default_bpm("upbeat")
    bed = sound.music_bed(12.0, style="upbeat")
    beat = int(60 / bpm * sound.SAMPLE_RATE)
    sub = np.fft.irfft(np.where(np.fft.rfftfreq(len(bed), 1 / sound.SAMPLE_RATE) < 120,
                                np.fft.rfft(bed[:, 0]), 0), len(bed))
    start = 4 * beat * 2  # past the fade-in and the drum-less first bar
    first = [np.abs(sub[start + k * beat: start + k * beat + beat // 8]).max() for k in range(4)]
    between = np.abs(sub[start + int(.3 * beat): start + int(.45 * beat)]).max()  # before the off-beat bass
    assert min(first) > 1.8 * between  # a thump on every beat, much quieter after it


def test_the_upbeat_bed_keeps_its_energy_out_of_the_speech_band():
    bed = sound.music_bed(20.0, style="upbeat")[:, 0]
    speech_band = _band_db(bed, *sound.SPEECH_BAND)
    assert speech_band < _db(bed) - 12  # the 1-4 kHz band sits well under the whole mix
    assert speech_band < _band_db(bed, 0, 400) - 12 and speech_band < _band_db(bed, 4000, 20000) - 6


def test_a_build_every_eight_bars_makes_the_phrase_differ_from_its_neighbour():
    bar = int(4 * 60 / sound.default_bpm("upbeat") * sound.SAMPLE_RATE)
    bed = sound.music_bed(30.0, style="upbeat")[:, 0]
    bars = [bed[k * bar:(k + 1) * bar] for k in range(1, 14)]
    assert _db(bars[6]) != pytest.approx(_db(bars[5]), abs=0.05)  # bar 8 (the roll) vs bar 7
    assert _db(bars[1]) == pytest.approx(_db(bars[3]), abs=1.0)  # ordinary bars are alike


def test_speech_sits_at_least_15_db_over_the_music_while_it_plays():
    from slidecast.models import Music

    music = Music("compose", volume=0.3, fade_in=1.0, fade_out=3.0, duck=True, style="upbeat")
    assert music.duck_db >= 18  # the upbeat bed ducks harder than the calm one
    assert Music("compose").duck_db == 12.0
    bed = sound.music_bed(20.0, style="upbeat")[:, 0]
    bed = bed / np.abs(bed).max() * 0.89  # as write_wav lays it down
    under_speech = bed * music.volume * 10 ** (-music.duck_db / 20)  # the ducking curve's floor
    # a stand-in voice: a 140 Hz buzz shaped to the speech band, at -20 dBFS RMS (a normalized narration)
    t = np.arange(len(bed)) / sound.SAMPLE_RATE
    voice = sum(np.sin(2 * np.pi * 140 * k * t) / k ** 0.5 for k in range(1, 30))
    voice = voice / np.sqrt(np.mean(voice ** 2)) * 10 ** (-20 / 20)
    assert _db(voice) - _db(under_speech) >= 15
    assert _band_db(voice, *sound.SPEECH_BAND) - _band_db(under_speech, *sound.SPEECH_BAND) >= 15


def test_a_long_upbeat_bed_loops_on_whole_phrases(tmp_path, monkeypatch):
    monkeypatch.setattr(sound, "LOOP_ABOVE", 10.0)
    seconds = sound.write_long_bed(tmp_path / "bed.wav", 200.0, style="upbeat")
    assert 200.0 <= seconds < 200.0 + 60.0
    bed = _read_bed(tmp_path / "bed.wav")
    assert np.abs(bed[0]).max() < 1e-3 and np.abs(bed[-1]).max() < 1e-3
    assert np.abs(bed[100 * sound.SAMPLE_RATE: 101 * sound.SAMPLE_RATE]).max() > 0.1


def test_compose_writes_the_upbeat_files(tmp_path):
    paths = sound.compose(tmp_path, length=3.0, style="upbeat")
    assert sorted(p.name for p in paths.values()) == ["intro_sting.wav", "music_bed.wav", "outro_sting.wav"]
    assert not np.array_equal(_read_bed(paths["music_bed"]), _read_bed(sound.compose(tmp_path / "c", length=3.0)["music_bed"]))
