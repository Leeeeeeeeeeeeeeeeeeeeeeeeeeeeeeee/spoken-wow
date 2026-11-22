"""Prepare pitch-preserving Spoken audio for the 1.1x–1.5x playback slider.

Install PyAV, then run this script with the game's Interface/AddOns directory.
Use --output to stage the files outside the game before copying them into
AddOns/Spoken/VoiceSpeed. Originals are only read. Re-running skips current
outputs and regenerates files whose source has changed.
"""

import argparse
import os
from concurrent.futures import ProcessPoolExecutor
from contextlib import ExitStack
from pathlib import Path

import av


SPEEDS = range(110, 151, 10)
AUDIO_EXTENSIONS = {".mp3", ".ogg", ".wav"}


def convert(job):
    source, relative, output = job
    source_mtime = source.stat().st_mtime_ns
    variants = []
    for speed in SPEEDS:
        target = output / str(speed) / relative.with_suffix(relative.suffix + ".mp3")
        if not target.is_file() or target.stat().st_mtime_ns < source_mtime:
            variants.append((speed, target, target.with_suffix(".tmp")))
    if not variants:
        return 0

    with ExitStack() as stack:
        original = stack.enter_context(av.open(str(source)))
        audio = original.streams.audio[0]
        graph = av.filter.Graph()
        input_node = graph.add_abuffer(
            sample_rate=audio.rate,
            format=audio.format.name,
            layout=audio.layout.name,
            time_base=audio.time_base,
        )
        split = graph.add("asplit", str(len(variants)))
        input_node.link_to(split)
        branches = []

        for index, (speed, target, temporary) in enumerate(variants):
            target.parent.mkdir(parents=True, exist_ok=True)
            stack.callback(temporary.unlink, missing_ok=True)
            encoded = stack.enter_context(av.open(str(temporary), "w", format="mp3"))
            stream = encoded.add_stream("libmp3lame", rate=24000)
            stream.layout = "mono"
            stream.bit_rate = 48000
            tempo = graph.add("atempo", str(speed / 100))
            sink = graph.add("abuffersink")
            split.link_to(tempo, index)
            tempo.link_to(sink)
            branches.append((sink, encoded, stream, temporary, target))

        graph.configure()
        for frame in original.decode(audio):
            input_node.push(frame)
            drain(branches)
        input_node.push(None)
        drain(branches)

        for _, encoded, stream, temporary, target in branches:
            encoded.mux(stream.encode(None))
            encoded.close()
            temporary.replace(target)
    return len(variants)


def drain(branches):
    for sink, encoded, stream, _, _ in branches:
        while True:
            try:
                frame = sink.pull()
            except (av.error.BlockingIOError, av.error.EOFError):
                break
            encoded.mux(stream.encode(frame))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("addons", type=Path, help="World of Warcraft Interface/AddOns folder")
    parser.add_argument("--output", type=Path, help="Destination for the VoiceSpeed folder")
    parser.add_argument("--jobs", type=int, default=min(12, os.cpu_count() or 1))
    args = parser.parse_args()
    output = args.output or args.addons / "Spoken" / "VoiceSpeed"
    sources = sorted(
        file for pack in args.addons.glob("Spoken*Audio*") if pack.is_dir()
        for file in pack.rglob("*") if file.is_file() and file.suffix.lower() in AUDIO_EXTENSIONS
    )
    if not sources:
        parser.error("No installed Spoken voice recordings found")
    print(f"Preparing {len(sources):,} recordings", flush=True)
    jobs = ((source, source.relative_to(args.addons), output) for source in sources)
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        generated = 0
        for index, count in enumerate(pool.map(convert, jobs, chunksize=8), 1):
            generated += count
            if index % 500 == 0 or index == len(sources):
                print(f"{index:,}/{len(sources):,} checked, {generated:,} files generated", flush=True)


if __name__ == "__main__":
    main()
