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
