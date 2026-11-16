import unittest
from unittest.mock import patch

import yaml

from services.ai import build_messages
from services.skills import APP_CONTRACT_SKILLS, CONTRACTS_DIR, SKILLS_DIR, SKILL_NAMES, load_skill


class SkillLoadingTests(unittest.TestCase):
    def test_each_process_has_its_own_skill(self):
        files = {path.parent.name: path for path in SKILLS_DIR.glob("*/SKILL.md")}
        self.assertEqual(SKILL_NAMES, set(files))
        self.assertTrue({"cucumber-gherkin", "karate-framework"} <= SKILL_NAMES)
        for name in SKILL_NAMES:
            with self.subTest(skill=name):
                _, metadata, body = files[name].read_text(encoding="utf-8").split("---\n", 2)
                metadata = yaml.safe_load(metadata)
                self.assertEqual(metadata["name"], name)
                self.assertTrue(metadata["description"].strip())
                self.assertTrue(body.strip())
                self.assertTrue(load_skill(name).startswith(body.strip()))
                self.assertNotIn(metadata["description"], load_skill(name))
                self.assertIs(load_skill(name), load_skill(name))

    def test_app_contracts_are_separate_and_only_used_by_registered_flows(self):
        self.assertEqual(APP_CONTRACT_SKILLS, {path.stem for path in CONTRACTS_DIR.glob("*.md") if path.name != "README.md"})
        for name in APP_CONTRACT_SKILLS:
            with self.subTest(skill=name):
                contract = (CONTRACTS_DIR / f"{name}.md").read_text(encoding="utf-8")
                self.assertTrue(load_skill(name).endswith(contract.strip()))

        load_skill.cache_clear()
        try:
            with patch("services.skills.CONTRACTS_DIR", SKILLS_DIR / "not-exported"):
                for name in ("cucumber-gherkin", "karate-framework"):
                    _, _, body = (SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8").split("---\n", 2)
                    self.assertEqual(load_skill(name), body.strip())
        finally:
            load_skill.cache_clear()

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
