"""Guards on the source files themselves."""
from pathlib import Path
import unittest

APP = Path(__file__).parent


class SourceTests(unittest.TestCase):
    def test_no_mis_encoded_text(self):
        # UTF-8 re-saved as if it were Windows-1252 turns characters such as the
        # ellipsis and arrows into three-character garbage in the window.
        for path in list(APP.rglob('*.py')) + list(APP.parent.glob('packaging/*.txt')):
            text = path.read_text(encoding='utf-8')
            for marker in (chr(0xE2) + chr(0x20AC), chr(0xE2) + chr(0x2020), chr(0xC3) + chr(0xA2)):
                self.assertNotIn(marker, text, f'mis-encoded text in {path.name}')


if __name__ == '__main__':
    unittest.main()
