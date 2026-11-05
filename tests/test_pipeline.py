import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import unittest
from pipeline import chunk_ranges, comparison, trim_overlap
from exports import export, transcript
from app import apply_worker_event
from visual_extract import clean_subtitle


class PipelineTests(unittest.TestCase):
    def test_long_speech_chunks_are_bounded_and_keep_overlap(self):
        chunks = chunk_ranges(16000 * 180, [{"start": 0, "end": 16000 * 180}])
        self.assertGreater(len(chunks), 6)
        self.assertEqual(chunks[0]["start_sample"], 0)
        self.assertEqual(chunks[-1]["end_sample"], 16000 * 180)
        for i, chunk in enumerate(chunks):
            self.assertLessEqual(chunk["end_sample"] - chunk["start_sample"], 28 * 16000)
            if i:
                self.assertLess(chunk["start_sample"], chunks[i-1]["end_sample"])

    def test_english_grammar_difference_cannot_hide_in_chinese(self):
        context = "今天我们学习英语语法，首先观察下面的例子。" * 10
        result = comparison(context + "He goes to school.", context + "He go to school.")
        self.assertTrue(result["english_difference"])
        self.assertGreater(result["similarity"], .9)
        self.assertEqual(result["review_state"], "needs_review")
        self.assertEqual(comparison("He go to school.", "He go to school.")["review_state"], "consistent")

    def test_review_events_update_matching_chunk_and_keep_original(self):
        job = {"segments": [{"id": 4, "text": "He go."}, {"id": 8, "text": "I am."}]}
        apply_worker_event(job, {"review": {"id": 4, "text": "He goes.", "raw_text": "He goes.", "segments": []}})
        self.assertEqual(job["segments"][0]["text"], "He go.")
        self.assertEqual(job["segments"][0]["review_state"], "needs_review")
        self.assertNotIn("review_text", job["segments"][1])
        self.assertEqual(job["review_count"], 1)

    def test_exports_preserve_provenance_and_incorrect_example(self):
        job = {"filename": "lesson.mp4", "segments": [{"id": 0, "start": 0, "end": 5, "text": "He go to school.", "review_text": "He goes to school.", "review_state": "needs_review"}],
               "visuals": [{"start": 2, "text": "He go to school. (incorrect)"}], "subtitles": [], "edited_text": None}
        self.assertEqual(transcript(job), "He go to school.")
        self.assertNotIn("incorrect", export(job, "txt"))
        study = export(job, "study")
        self.assertIn("画面 OCR", study)
        self.assertIn("待核对，勿据此判分", study)
        self.assertIn("He go to school. (incorrect)", study)
        self.assertIn("不要擅自改正", study)

    def test_resolution_does_not_destroy_raw_transcript(self):
        job = {"segments": [{"start": 0, "end": 1, "text": "raw", "resolved_text": "chosen"}], "edited_text": None}
        self.assertEqual(transcript(job), "chosen")
        self.assertIn("raw", export(job, "srt"))

    def test_short_repeated_teaching_word_is_not_removed(self):
        self.assertEqual(trim_overlap("He is", "is is a verb"), "is is a verb")
        self.assertEqual(trim_overlap("Please keep this example.", "keep this example. The next one."), "The next one.")

    def test_subtitle_control_sequences(self):
        self.assertEqual(clean_subtitle("0,0,Default,,0,0,0,,{\\b1}He go.\\NWrong."), "He go.\nWrong.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
