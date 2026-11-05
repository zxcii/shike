import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from visual_extract import recognize_image

text = "\n".join(item["text"] for item in recognize_image(Path(__file__).parent / "assets" / "mixed-ocr.png"))
print(text)
assert "He go to school every day." in text
assert "He went to school yesterday." in text
print("PASS: native OCR preserves intentionally incorrect example")
