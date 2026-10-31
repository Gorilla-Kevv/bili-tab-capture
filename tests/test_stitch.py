"""自动裁剪拼接缝算法的合成图测试。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tab_extractor as t  # noqa: E402


def make_score(width: int = 1000, height: int = 240) -> Image.Image:
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    for y in range(40, 200, 12):
        draw.line((0, y, width, y), fill="black", width=1)
    for measure, x in enumerate(range(20, width, 80)):
        draw.line((x, 30, x, 210), fill="black", width=3)
        # 每个小节的音符位置不同，避免周期图案让位移估计落在别的周期上。
        for note in range(4):
            nx = x + 10 + note * 16
            top = 55 + ((measure * 37 + note * 53) % 110)
            draw.rectangle((nx, top, nx + 10, top + 12), fill="black")
    return image


class StitchTests(unittest.TestCase):
    def test_scroll_shift_and_barline_seam(self):
        long_score = make_score(1000)
        left = long_score.crop((0, 0, 500, 240))
        right = long_score.crop((160, 0, 660, 240))
        details = t.compute_stitch_details([left, right])
        self.assertTrue(details["valid"][0])
        self.assertFalse(details["duplicate"][0])
        self.assertGreater(details["confidence"][0], 0.9)
        right_edge, left_edge = details["seams"][0]
        # 切口在共享区内的小节线上，隐含位移仍应接近 160/500。
        self.assertAlmostEqual(right_edge - left_edge, 160 / 500, delta=0.03)
        self.assertEqual(len(t.compute_stitch_seams([left, right])), 1)

    def test_near_duplicate_is_marked(self):
        image = make_score(500, 240)
        details = t.compute_stitch_details([image, image.copy()])
        self.assertTrue(details["duplicate"][0])
        self.assertFalse(details["valid"][0])
        self.assertEqual(details["seams"][0], [1.0, 0.0])

    def test_flat_unrelated_pair_is_low_confidence(self):
        white = Image.new("RGB", (500, 240), "white")
        black = Image.new("RGB", (500, 240), "black")
        details = t.compute_stitch_details([white, black])
        self.assertFalse(details["valid"][0])
        self.assertEqual(details["seams"][0], [1.0, 0.0])


if __name__ == "__main__":
    unittest.main()
