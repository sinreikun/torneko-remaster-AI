"""Mathematical equivalence, not simulated gameplay."""
import random,unittest
from PIL import Image,ImageFilter
from autoplayer.image_math import maximum3

class ImageMathContracts(unittest.TestCase):
    def test_matches_full_neighborhood_maximum_including_edges(self):
        rng=random.Random(7)
        for w,h in [(1,1),(1,7),(8,1),(2,2),(17,13),(63,48)]:
            for passes in (1,2,4):
                im=Image.frombytes('L',(w,h),bytes(rng.randrange(256) for _ in range(w*h)));expected=im;actual=im
                for _ in range(passes):expected=expected.filter(ImageFilter.MaxFilter(3));actual=maximum3(actual)
                self.assertEqual(actual.tobytes(),expected.tobytes(),(w,h,passes))
