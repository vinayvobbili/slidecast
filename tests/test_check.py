"""slidecast check: speech frames from levels, and the quiet each slide change gets either side."""

from slidecast import check as chk
from slidecast import cli


def test_speech_is_frames_near_the_loud_ones():
    levels = [-80, -20, -22, -60, -21, -79]
    assert chk.speech(levels) == [False, True, True, False, True, False]
    assert chk.speech([]) == []


def test_a_bed_swelling_in_a_long_hold_is_not_speech():
    # Voice at -25 dB, then a 75 s silent hold (50 ms frames) whose bed floats at -50 and now and then
    # crosses the -43 line for a few frames; the voice returns at the end.
    hold = [-50.0] * 1500
    for at in (100, 400, 401, 402, 403, 900, 901, 1300, 1301, 1302, 1303, 1304):
        hold[at] = -40.0
    levels = [-25.0] * 100 + hold + [-25.0] * 100
    talking = chk.speech(levels)
    assert not any(talking[100:1600])
    assert all(talking[:100]) and all(talking[1600:])
    # The change at the end of the hold has the whole hold of quiet before it, not a bed blip's few frames.
    (c,) = chk.cuts([1600 * 0.05], talking)
    assert c.quiet_before == 75.0


def test_a_burst_with_a_quiet_gap_stays_whole_and_a_cut_into_it_is_caught():
    # A word with a 0.15 s dip inside it and a soft tail 12 dB under its peak, then a change right after the tail.
    levels = [-70.0] * 30 + [-25.0] * 4 + [-60.0] * 3 + [-37.0] * 4 + [-70.0] * 30
    talking = chk.speech(levels)
    assert talking[30:41] == [True] * 4 + [False] * 3 + [True] * 4
    (c,) = chk.cuts([41 * 0.05], talking)
    assert c.quiet_before == 0.0


def test_levels_option_prints_frames(monkeypatch, capsys):
    monkeypatch.setattr(chk, "speech_levels", lambda video: [-70.0, -25.0, -26.0, -70.0])
    assert cli.main(["check", "out.mp4", "--levels", "0.05", "0.15"]) == 0
    rows = capsys.readouterr().out.splitlines()
    assert len(rows) == 2 and all(r.rstrip().endswith("S") for r in rows) and "-25.0 dB" in rows[0]


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
