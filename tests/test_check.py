"""slidecast check: speech frames from levels, and the quiet each slide change gets either side."""

from slidecast import check as chk
from slidecast import cli


def test_speech_is_frames_near_the_loud_ones():
    levels = [-80, -20, -22, -60, -21, -79]
    assert chk.speech(levels) == [False, True, True, False, True, False]
    assert chk.speech([]) == []


def test_cuts_measure_quiet_before_and_after():
    # Speech in frames 0-3 and 10-12; a change at frame 6 (0.30 s) has 2 quiet frames before, 4 after.
    talking = [True] * 4 + [False] * 6 + [True] * 3
    (c,) = chk.cuts([0.30], talking, hop=0.05)
    assert (c.quiet_before, c.quiet_after) == (0.1, 0.2)
    # A change mid-sentence has none either side.
    assert chk.cuts([0.10], talking, hop=0.05)[0].quiet_before == 0.0


def test_cli_check_flags_a_change_that_cuts_in(monkeypatch, capsys):
    found = [chk.Cut(5.0, 1.2, 0.4), chk.Cut(9.0, 0.1, 0.3)]
    monkeypatch.setattr(chk, "check", lambda video: found)
    assert cli.main(["check", "out.mp4", "--min-gap", "0.6"]) == 1
    out = capsys.readouterr().out
    assert "9.00 s   quiet 0.10 s before" in out and "← cuts in" in out
    assert "least quiet before one: 0.10 s at 9.00 s" in out
    assert cli.main(["check", "out.mp4"]) == 0
