from pathlib import Path

import pytest

from slidecast import (Music, Sting, build_clip_segment, build_segment, concat, master,
                       mix_music, poster)
from slidecast.video import DUCK
from tests.fakes import FakeRunner


def _filter_complex(cmd):
    return cmd[cmd.index("-filter_complex") + 1]


def test_master_without_music_fades_and_holds():
    runner = FakeRunner()
    master(Path("in.mp4"), Path("out.mp4"),
           fade_in=0.8, fade_out=1.2, end_hold=2.5,
           total_duration=10.0, ffmpeg="ff", runner=runner)
    cmd = runner.commands[0]
    fc = _filter_complex(cmd)
    # total = 10 + 2.5 = 12.5; fade-out starts at 12.5 - 1.2 = 11.3
    assert "tpad=stop_mode=clone:stop_duration=2.500" in fc
    assert "fade=t=in:st=0:d=0.800" in fc
    assert "fade=t=out:st=11.300:d=1.200" in fc
    assert "apad=pad_dur=2.500" in fc
    assert "amix" not in fc  # no music -> no mixer
    assert cmd.count("-i") == 1
    assert "-stream_loop" not in cmd
    assert cmd[-1] == "out.mp4"


def test_master_with_music_loops_attenuates_and_mixes():
    runner = FakeRunner()
    master(Path("in.mp4"), Path("out.mp4"),
           music=Path("bed.mp3"), fade_in=0.8, fade_out=1.2, end_hold=2.5,
           music_volume=0.08, music_fade=1.5,
           total_duration=10.0, ffmpeg="ff", runner=runner)
    cmd = runner.commands[0]
    # music is the second input and is looped infinitely
    assert "-stream_loop" in cmd and cmd[cmd.index("-stream_loop") + 1] == "-1"
    assert cmd.index("in.mp4") < cmd.index("bed.mp3")
    fc = _filter_complex(cmd)
    assert "volume=0.08" in fc
    assert "atrim=0:12.500" in fc  # bed trimmed to total length
    assert "afade=t=out:st=11.000:d=1.500" in fc  # 12.5 - 1.5
    assert "amix=inputs=2:duration=first:dropout_transition=0:normalize=0" in fc
    # both legs upmixed to stereo so the mix isn't collapsed to mono by the mono narration
    assert fc.count("aformat=sample_rates=48000:channel_layouts=stereo") == 2
    assert cmd[cmd.index("-map") + 1] == "[v]"


def test_master_can_skip_video_fades():
    runner = FakeRunner()
    master(Path("in.mp4"), Path("out.mp4"),
           fade_in=0, fade_out=0, end_hold=0,
           total_duration=5.0, ffmpeg="ff", runner=runner)
    fc = _filter_complex(runner.commands[0])
    assert "fade=" not in fc
    assert "tpad" not in fc
    assert "format=yuv420p" in fc


def test_build_segment_with_known_duration_pads_to_target():
    runner = FakeRunner()
    build_segment(Path("img.png"), Path("a.wav"), Path("out.mp4"),
                  width=1280, height=720, fps=25, duration=4.2,
                  ffmpeg="ff", runner=runner)
    cmd = runner.commands[0]
    assert cmd[0] == "ff"
    assert "-t" in cmd and cmd[cmd.index("-t") + 1] == "4.200"
    assert "-af" in cmd and cmd[cmd.index("-af") + 1] == "apad"
    assert "-shortest" not in cmd
    assert "scale=1280:720" in cmd
    assert cmd[cmd.index("-r") + 1] == "25"
    assert "+faststart" in cmd
    assert cmd[-1] == "out.mp4"


def test_build_segment_without_duration_uses_shortest():
    runner = FakeRunner()
    build_segment(Path("img.png"), Path("a.wav"), Path("out.mp4"),
                  width=1920, height=1080, duration=None,
                  ffmpeg="ff", runner=runner)
    cmd = runner.commands[0]
    assert "-shortest" in cmd
    assert "-t" not in cmd
    assert "scale=1920:1080" in cmd


def test_concat_writes_listfile_and_copies(tmp_path):
    runner = FakeRunner()
    segs = [tmp_path / "a.mp4", tmp_path / "b.mp4"]
    for s in segs:
        s.write_bytes(b"x")
    out = concat(segs, tmp_path / "final.mp4", ffmpeg="ff", runner=runner)
    cmd = runner.commands[0]
    assert "concat" in cmd
    assert cmd[cmd.index("-c") + 1] == "copy"
    assert str(out) == str(tmp_path / "final.mp4")


def test_concat_rejects_empty():
    with pytest.raises(ValueError):
        concat([], Path("out.mp4"), ffmpeg="ff", runner=FakeRunner())


def test_poster_grabs_one_frame():
    runner = FakeRunner()
    poster(Path("in.mp4"), Path("p.jpg"), ffmpeg="ff", runner=runner)
    cmd = runner.commands[0]
    assert cmd[cmd.index("-frames:v") + 1] == "1"
    assert cmd[-1] == "p.jpg"


def test_build_clip_segment_letterboxes_holds_and_replaces_audio():
    runner = FakeRunner()
    build_clip_segment(Path("clip.webm"), Path("a.wav"), Path("out.mp4"),
                       width=1280, height=720, fps=25, duration=9.5, hold=2.0,
                       ffmpeg="ff", runner=runner)
    cmd = runner.commands[0]
    graph = _filter_complex(cmd)
    assert "force_original_aspect_ratio=decrease" in graph and "pad=1280:720" in graph
    assert "tpad=stop_mode=clone:stop_duration=2.000" in graph
    assert "[1:a]apad[a]" in graph  # narration, not the clip's own audio
    assert cmd[cmd.index("-t") + 1] == "9.500"
    # Same encoder settings as build_segment, so slides and clips concat with -c copy.
    assert cmd[cmd.index("-c:v") + 1] == "libx264" and "stillimage" in cmd
    assert cmd[cmd.index("-r") + 1] == "25"


def test_build_clip_segment_without_hold_has_no_tpad():
    runner = FakeRunner()
    build_clip_segment(Path("c.mp4"), Path("a.wav"), Path("o.mp4"),
                       width=640, height=400, duration=3.0, ffmpeg="ff", runner=runner)
    assert "tpad" not in _filter_complex(runner.commands[0])


def test_poster_can_skip_past_a_fade_in():
    runner = FakeRunner()
    poster(Path("v.mp4"), Path("p.jpg"), at=0.8, ffmpeg="ff", runner=runner)
    cmd = runner.commands[0]
    assert cmd[cmd.index("-ss") + 1] == "0.800" and cmd.index("-ss") < cmd.index("-i")


def test_mix_music_loops_trims_fades_ducks_and_copies_video():
    runner = FakeRunner()
    mix_music(Path("in.mp4"), Path("out.mp4"), Music("bed.mp3"),
              total_duration=20.0, ffmpeg="ff", runner=runner)
    cmd = runner.commands[0]
    assert cmd[cmd.index("-stream_loop") + 1] == "-1" and cmd.index("-stream_loop") < cmd.index("bed.mp3")
    assert cmd[cmd.index("-c:v") + 1] == "copy"  # video untouched, audio re-encoded
    assert cmd[cmd.index("-c:a") + 1] == "aac"
    assert cmd[cmd.index("-map") + 1] == "0:v"
    fc = _filter_complex(cmd)
    assert fc == (
        "[0:a]apad=whole_dur=20.000,aformat=sample_rates=48000:channel_layouts=stereo[narr];"
        "[narr]asplit=2[voice][key];"
        "[1:a]atrim=0:20.000,asetpts=PTS-STARTPTS,volume=0.22,"
        "aformat=sample_rates=48000:channel_layouts=stereo[music];"
        f"[music][key]sidechaincompress={DUCK},"
        "afade=t=in:st=0:d=1.000,afade=t=out:st=18.000:d=2.000[bed];"
        "[voice][bed]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
        "alimiter=limit=0.95:level=0[a]"
    )
    assert DUCK == "threshold=0.02:ratio=8:attack=20:release=400"


def test_mix_music_with_speech_times_plans_the_dip_instead_of_compressing():
    runner = FakeRunner()
    mix_music(Path("in.mp4"), Path("out.mp4"), Music("bed.mp3", duck_db=6),
              outro="out.wav", total_duration=20.0, outro_duration=4.0,
              speech=[(1.0, 5.0), (7.0, 12.0)], ffmpeg="ff", runner=runner)
    fc = _filter_complex(runner.commands[0])
    assert "sidechaincompress" not in fc and "asplit" not in fc
    curve = ("max(max(clip((t-0.750)/0.25,0,1)*clip((5.600-t)/0.6,0,1),"
             "clip((t-6.750)/0.25,0,1)*clip((12.600-t)/0.6,0,1)),"
             "clip((t-15.750)/0.25,0,1)*clip((20.600-t)/0.6,0,1))")  # the outro: 16-20 s
    assert (f"[1:a]atrim=0:20.000,asetpts=PTS-STARTPTS,volume=0.22,"
            f"aformat=sample_rates=48000:channel_layouts=stereo,"
            f"asetnsamples=n=480,volume='0.22*(1-0.4988*{curve})':eval=frame,"
            f"afade=t=in:st=0:d=1.000,afade=t=out:st=18.000:d=2.000[bed]") in fc
    assert "[narr][outro]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[voice]" in fc


def test_mix_music_without_ducking_just_lays_the_bed_under():
    runner = FakeRunner()
    mix_music(Path("in.mp4"), Path("out.mp4"),
              Music("bed.mp3", volume=0.1, fade_in=0, fade_out=0.5, duck=False),
              total_duration=8.0, ffmpeg="ff", runner=runner)
    fc = _filter_complex(runner.commands[0])
    assert "sidechaincompress" not in fc and "asplit" not in fc
    assert "afade=t=in" not in fc  # a 0 s fade is skipped, not passed to ffmpeg
    assert "volume=0.1,aformat=sample_rates=48000:channel_layouts=stereo," \
           "afade=t=out:st=7.500:d=0.500[bed]" in fc
    assert "[narr][bed]amix=inputs=2" in fc


def test_mix_music_places_the_intro_at_zero_and_ends_the_outro_with_the_reel():
    runner = FakeRunner()
    mix_music(Path("in.mp4"), Path("out.mp4"), Music("bed.mp3"),
              intro="in.wav", outro=Sting("out.wav", volume=0.5),
              total_duration=30.0, outro_duration=4.25, ffmpeg="ff", runner=runner)
    cmd = runner.commands[0]
    # inputs: 0 the reel, 1 the looped bed, 2 the intro, 3 the outro
    assert [cmd[i + 1] for i, a in enumerate(cmd) if a == "-i"] == \
        ["in.mp4", "bed.mp3", "in.wav", "out.wav"]
    fc = _filter_complex(cmd)
    assert "[2:a]volume=0.75,aformat" in fc  # intro: default level, no delay
    assert "[3:a]volume=0.5,adelay=25750:all=1,aformat" in fc  # 30 - 4.25 s
    # Narration and stings form the voice; it keys the duck, so the bed dips under all three.
    assert "[narr][intro][outro]amix=inputs=3:duration=first:dropout_transition=0:normalize=0," \
           "asplit=2[voice][key]" in fc
    assert "[music][key]sidechaincompress" in fc
    assert "[voice][bed]amix=inputs=2" in fc


def test_mix_music_stings_alone_need_no_bed():
    runner = FakeRunner()
    mix_music(Path("in.mp4"), Path("out.mp4"), outro="out.wav",
              total_duration=3.0, outro_duration=5.0, ffmpeg="ff", runner=runner)
    cmd = runner.commands[0]
    assert "-stream_loop" not in cmd
    fc = _filter_complex(cmd)
    assert "[1:a]volume=0.7,aformat" in fc  # longer than the reel: starts at 0, no delay
    assert "adelay" not in fc and "sidechaincompress" not in fc
    assert fc.endswith("[narr][outro]amix=inputs=2:duration=first:dropout_transition=0:"
                       "normalize=0,alimiter=limit=0.95:level=0[a]")


def test_mix_music_needs_something_to_mix():
    with pytest.raises(ValueError):
        mix_music(Path("in.mp4"), Path("out.mp4"), total_duration=1.0,
                  ffmpeg="ff", runner=FakeRunner())


def test_loudness_target_normalizes_the_final_mix():
    runner = FakeRunner()
    mix_music(Path("in.mp4"), Path("out.mp4"), Music("bed.mp3"), loudness=-16,
              total_duration=10.0, ffmpeg="ff", runner=runner)
    fc = _filter_complex(runner.commands[0])
    assert fc.endswith("normalize=0,alimiter=limit=0.95:level=0,"
                       "loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000[a]")


def test_loudness_alone_normalizes_the_narration():
    runner = FakeRunner()
    master(Path("in.mp4"), Path("out.mp4"), fade_in=0, fade_out=0, end_hold=1.0,
           loudness=-14.5, total_duration=5.0, ffmpeg="ff", runner=runner)
    fc = _filter_complex(runner.commands[0])
    assert fc.endswith("[0:a]apad=pad_dur=1.000,loudnorm=I=-14.5:TP=-1.5:LRA=11,aresample=48000[a]")
    assert "amix" not in fc and "alimiter" not in fc


def test_master_mixes_music_and_stings_in_its_one_pass():
    runner = FakeRunner()
    bed = Music("bed.mp3")
    master(Path("in.mp4"), Path("out.mp4"), music=bed, intro="in.wav", outro="out.wav",
           fade_in=0.5, fade_out=0.5, end_hold=2.0, music_volume=0.3,
           total_duration=10.0, outro_duration=3.0, ffmpeg="ff", runner=runner)
    cmd = runner.commands[0]
    assert cmd[cmd.index("-c:v") + 1] == "libx264"  # the fades need a re-encode
    fc = _filter_complex(cmd)
    assert fc.startswith("[0:v]tpad=stop_mode=clone:stop_duration=2.000,")
    assert "[0:a]apad=pad_dur=2.000," in fc
    assert "adelay=9000:all=1" in fc  # outro ends on the last held frame: 12 - 3
    assert "volume=0.3," in fc and "sidechaincompress" in fc
    assert bed.volume == 0.22  # the override doesn't touch the caller's Music


def test_segments_can_hold_silent_before_the_narration():
    runner = FakeRunner()
    build_segment(Path("img.png"), Path("a.wav"), Path("out.mp4"),
                  width=640, height=360, duration=4.0, delay=1.8, ffmpeg="ff", runner=runner)
    cmd = runner.commands[0]
    assert cmd[cmd.index("-af") + 1] == "adelay=1800:all=1,apad"
    assert cmd[cmd.index("-t") + 1] == "4.000"

    build_clip_segment(Path("c.mp4"), Path("a.wav"), Path("o.mp4"), width=640, height=360,
                       duration=5.0, hold=1.0, delay=1.5, ffmpeg="ff", runner=runner)
    graph = _filter_complex(runner.commands[1])
    assert "tpad=start_mode=clone:start_duration=1.500" in graph
    assert "[1:a]adelay=1500:all=1,apad[a]" in graph
