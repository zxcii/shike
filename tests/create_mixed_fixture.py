"""Generate a bilingual grammar lesson fixture with visible English examples."""
import sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1]))
import project_env
from fractions import Fraction
from pathlib import Path
import subprocess
import argparse
import av
import numpy as np
from PIL import Image, ImageDraw, ImageFont


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeat", type=int, default=1)
    args = parser.parse_args()
    assert 1 <= args.repeat <= 30
    directory = Path(__file__).parent / "assets"
    pieces = [
        ("Tingting (中文（中国大陆）)", "今天我们学习一般现在时和一般过去时。请注意下面的英文句子。"),
        ("Samantha (英语（美国）)", "He goes to school every day. He went to school yesterday."),
        ("Tingting (中文（中国大陆）)", "第三个句子是一个错误的例句，请保留原样，稍后我们会分析错误。"),
        ("Samantha (英语（美国）)", "He go to school every day. This sentence is incorrect."),
        ("Tingting (中文（中国大陆）)", "复习的时候，请比较这几个句子的动词形式，然后解释它们的区别。"),
    ]
    arrays = []
    for index, (voice, text) in enumerate(pieces):
        path = directory / f"mixed-{index}.aiff"
        subprocess.run(["/usr/bin/say", "-v", voice, "-r", "160", "-o", str(path), text], check=True)
        with av.open(str(path)) as container:
            resampler = av.AudioResampler(format="fltp", layout="mono", rate=24000)
            for frame in container.decode(audio=0):
                arrays.extend(f.to_ndarray() for f in resampler.resample(frame))
            arrays.extend(f.to_ndarray() for f in resampler.resample(None))
        arrays.append(np.zeros((1, 12000), dtype=np.float32))
    audio = np.concatenate(arrays, axis=1)
    audio = np.tile(audio, (1, args.repeat))
    duration = audio.shape[1] / 24000
    font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 38)
    small = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 23)
    image = Image.new("RGB", (960, 540), "#f5f0fa")
    draw = ImageDraw.Draw(image)
    draw.text((55, 100), "Grammar lesson: present and past", font=font, fill="#554267")
    for index, line in enumerate(["He goes to school every day.", "He went to school yesterday.", "He go to school every day. (incorrect)"]):
        draw.text((55, 210 + index * 70), line, font=small, fill="#755a91")
    draw.text((55, 475), "Keep the incorrect example unchanged.", font=small, fill="#927ea6")
    image.save(directory / "mixed-ocr.png")
    video_path = directory / ("中英混合长分段示例.mp4" if args.repeat > 1 else "中英混合语法课示例.mp4")
    with av.open(str(video_path), "w") as output:
        video = output.add_stream("libx264", rate=1)
        video.width, video.height, video.pix_fmt = 960, 540, "yuv420p"
        video.options = {"preset": "ultrafast", "crf": "25"}
        sound = output.add_stream("aac", rate=24000)
        sound.layout = "mono"
        sound.bit_rate = 64000
        position = 0
        for second in range(int(np.ceil(duration))):
            frame = av.VideoFrame.from_image(image)
            frame.pts, frame.time_base = second, Fraction(1, 1)
            for packet in video.encode(frame):
                output.mux(packet)
            end = min((second + 1) * 24000, audio.shape[1])
            while position < end:
                piece = np.ascontiguousarray(audio[:, position:min(position + 1024, end)])
                frame = av.AudioFrame.from_ndarray(piece, format="fltp", layout="mono")
                frame.sample_rate, frame.pts, frame.time_base = 24000, position, Fraction(1, 24000)
                for packet in sound.encode(frame):
                    output.mux(packet)
                position += piece.shape[1]
        for stream in (video, sound):
            for packet in stream.encode(None):
                output.mux(packet)
    print(f"Created {video_path}: {duration:.1f} seconds")


if __name__ == "__main__":
    main()
