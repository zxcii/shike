"""Build a small real MP4 fixture with a Chinese lesson audio track."""
import argparse
from fractions import Fraction
from pathlib import Path
import av
import numpy as np


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutes", type=float, default=0)
    args = parser.parse_args()
    folder = Path(__file__).parent / "assets"
    source = folder / "学习方法示例.aiff"
    destination = folder / ("12分钟学习示例.mp4" if args.minutes else "学习方法示例.mp4")
    with av.open(str(source)) as container:
        arrays = []
        resampler = av.AudioResampler(format="fltp", layout="mono", rate=24000)
        for frame in container.decode(audio=0):
            arrays.extend(f.to_ndarray() for f in resampler.resample(frame))
        arrays.extend(f.to_ndarray() for f in resampler.resample(None))
    audio = np.concatenate(arrays, axis=1)
    if args.minutes:
        target = int(args.minutes * 60 * 24000)
        silence = np.zeros((1, 24000 * 8), dtype=np.float32)
        repeated = np.concatenate([audio, silence], axis=1)
        audio = np.tile(repeated, (1, target // repeated.shape[1] + 1))[:, :target]
    duration = audio.shape[1] / 24000
    with av.open(str(destination), "w") as output:
        video = output.add_stream("libx264", rate=1)
        video.width, video.height, video.pix_fmt = 640, 360, "yuv420p"
        video.options = {"preset": "ultrafast", "crf": "30"}
        sound = output.add_stream("aac", rate=24000)
        sound.layout = "mono"
        sound.bit_rate = 48000
        frame_image = np.zeros((360, 640, 3), dtype=np.uint8)
        frame_image[:] = [242, 238, 249]
        for i, height in enumerate([40, 70, 115, 80, 150, 95, 55, 100, 65]):
            frame_image[180-height//2:180+height//2, 190+i*28:202+i*28] = [134, 109, 193]
        audio_position = 0
        for second in range(int(np.ceil(duration))):
            image = av.VideoFrame.from_ndarray(frame_image, format="rgb24")
            image.pts = second
            image.time_base = Fraction(1, 1)
            for packet in video.encode(image):
                output.mux(packet)
            end = min((second + 1) * 24000, audio.shape[1])
            while audio_position < end:
                piece = np.ascontiguousarray(audio[:, audio_position:min(audio_position+1024, end)])
                frame = av.AudioFrame.from_ndarray(piece, format="fltp", layout="mono")
                frame.sample_rate = 24000
                frame.pts = audio_position
                frame.time_base = Fraction(1, 24000)
                for packet in sound.encode(frame):
                    output.mux(packet)
                audio_position += piece.shape[1]
        for stream in (video, sound):
            for packet in stream.encode(None):
                output.mux(packet)
    print(f"Created {destination}: {duration:.1f} seconds, {destination.stat().st_size / 1024**2:.1f} MB", flush=True)


if __name__ == "__main__":
    main()
