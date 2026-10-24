"""The interface scale: 100 % by default, and the 75 % that 0.6.0-beta.1 set by default moves to 100 % once."""
import tests  # noqa: F401
import unittest

from app import database


def _scale() -> str:
    conn = database.get_connection()
    try:
        return conn.execute("SELECT value FROM settings WHERE key = 'ui_scale'").fetchone()[0]
    finally:
        conn.close()


def _put(value: str, marker: bool) -> None:
    conn = database.get_connection()
    try:
        conn.execute("UPDATE settings SET value = ? WHERE key = 'ui_scale'", (value,))
        if not marker:
            conn.execute("DELETE FROM settings WHERE key = 'ui_scale_default_v2'")
        conn.commit()
    finally:
        conn.close()


class UiScaleDefaultTest(unittest.TestCase):
    def setUp(self):
        database.init_db()

    def test_default_is_100(self):
        conn = database.get_connection()  # a library that has no scale yet
        conn.execute("DELETE FROM settings WHERE key = 'ui_scale'")
        conn.commit()
        conn.close()
        database.init_db()
        self.assertEqual(_scale(), "100")

    def test_the_old_default_moves_to_100_once(self):
        _put("75", marker=False)
        database.init_db()
        self.assertEqual(_scale(), "100")
        _put("75", marker=True)  # chosen by hand after the switch: stays
        database.init_db()
        self.assertEqual(_scale(), "75")

    def test_a_scale_chosen_by_hand_stays(self):
        _put("90", marker=False)
        database.init_db()
        self.assertEqual(_scale(), "90")


if __name__ == "__main__":
    unittest.main()
