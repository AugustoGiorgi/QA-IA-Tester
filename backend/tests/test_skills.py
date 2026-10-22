import unittest

from services.ai import build_messages
from services.skills import SKILL_NAMES, load_skill


class SkillLoadingTests(unittest.TestCase):
    def test_each_process_has_its_own_skill(self):
        self.assertEqual(len(SKILL_NAMES), 11)
        for name in SKILL_NAMES:
            with self.subTest(skill=name):
                self.assertTrue(load_skill(name).strip())
                self.assertIs(load_skill(name), load_skill(name))

    def test_unknown_skill_cannot_read_arbitrary_file(self):
        with self.assertRaises(ValueError):
            load_skill("../services/ai.py")

    def test_only_selected_skill_and_nonempty_feedback_are_sent(self):
        messages = build_messages("casos-prueba", "Documento de ejemplo")
        self.assertEqual([item["role"] for item in messages], ["system", "user"])
        self.assertEqual(messages[0]["content"], load_skill("casos-prueba"))
        self.assertNotIn(load_skill("sql-chat"), messages[0]["content"])
        self.assertEqual(messages[1]["content"], "Documento de ejemplo")
        self.assertNotIn("Retroalimentacion", messages[1]["content"])

        with_feedback = build_messages("entendimiento", "Documento", ["Revisar estado"])
        self.assertIn("Revisar estado", with_feedback[1]["content"])


if __name__ == "__main__":
    unittest.main()
