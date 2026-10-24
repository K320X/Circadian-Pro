"""Keep both supported languages complete across Home Assistant UI surfaces."""

from __future__ import annotations

import ast
import json
from pathlib import Path
import re
import unittest


COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "circadian_pro"


def _unique_object(pairs):
    """Reject duplicate JSON keys instead of silently discarding translations."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate translation key: {key}")
        result[key] = value
    return result


def _catalog(filename):
    return json.loads(
        (COMPONENT / filename).read_text(encoding="utf-8"),
        object_pairs_hook=_unique_object,
    )


def _leaves(value, prefix=()):
    result = {}
    for name, child in value.items():
        path = (*prefix, name)
        if isinstance(child, dict):
            result.update(_leaves(child, path))
        else:
            result[path] = child
    return result


def _action_fields():
    """Read action/field identifiers from the flat services.yaml structure."""
    result = {}
    action = None
    for line in (COMPONENT / "services.yaml").read_text(encoding="utf-8").splitlines():
        if match := re.fullmatch(r"(\w+):", line):
            action = match[1]
            result[action] = set()
        elif match := re.fullmatch(r"    (\w+):", line):
            result[action].add(match[1])
    return result


class TranslationCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = _catalog("strings.json")
        cls.catalogs = {
            "en": _catalog("translations/en.json"),
            "es": _catalog("translations/es.json"),
        }

    def test_english_translation_matches_source_and_json_keys_are_unique(self):
        self.assertEqual(self.source, self.catalogs["en"])

    def test_every_translation_has_matching_nonempty_keys_and_placeholders(self):
        source = _leaves(self.source)
        for language, catalog in self.catalogs.items():
            translated = _leaves(catalog)
            self.assertEqual(set(translated), set(source), language)
            for path, text in translated.items():
                with self.subTest(language=language, path=path):
                    self.assertIsInstance(text, str)
                    self.assertTrue(text.strip())
                    self.assertEqual(
                        set(re.findall(r"\{([^{}]+)\}", text)),
                        set(re.findall(r"\{([^{}]+)\}", source[path])),
                    )

    def test_spanish_does_not_silently_fall_back_to_english(self):
        # Product names and the word Natural are identical in both languages.
        shared_text = {
            ("title",),
            ("selector", "brightness_mode", "options", "default"),
            ("entity", "switch", "circadian_pro", "name"),
        }
        english = _leaves(self.catalogs["en"])
        spanish = _leaves(self.catalogs["es"])
        self.assertEqual(
            {path for path, text in english.items() if spanish[path] == text},
            shared_text,
        )

    def test_every_options_field_has_a_label_and_help_in_both_languages(self):
        for language, catalog in self.catalogs.items():
            for step, strings in catalog["options"]["step"].items():
                with self.subTest(language=language, step=step):
                    self.assertEqual(set(strings["data"]), set(strings["data_description"]))

    def test_native_general_form_fields_are_covered_in_both_languages(self):
        tree = ast.parse((COMPONENT / "config_flow.py").read_text(encoding="utf-8"))
        fields = next(
            ast.literal_eval(node.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "allowed_init_keys" for target in node.targets)
        )
        minute_fields = {
            "sunrise_offset", "sunset_offset",
            "brightness_mode_time_light", "brightness_mode_time_dark",
        }
        visible_fields = {f"{field}_minutes" if field in minute_fields else field for field in fields}
        for language, catalog in self.catalogs.items():
            self.assertTrue(visible_fields <= set(catalog["options"]["step"]["init"]["data"]), language)

    def test_all_native_actions_and_their_fields_are_translated(self):
        actions = _action_fields()
        self.assertTrue(actions)
        for language, catalog in self.catalogs.items():
            self.assertEqual(set(actions), set(catalog["services"]), language)
            for action, fields in actions.items():
                translated = catalog["services"][action]
                self.assertTrue(translated["name"])
                self.assertTrue(translated["description"])
                self.assertEqual(fields, set(translated["fields"]), (language, action))
                for field in fields:
                    self.assertEqual(set(translated["fields"][field]), {"name", "description"})

    def test_native_action_metadata_is_not_hardcoded_in_yaml(self):
        yaml = (COMPONENT / "services.yaml").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"^\s+(name|description):", yaml, re.MULTILINE))

    def test_native_action_selectors_have_translated_choices(self):
        source = (COMPONENT / "services.yaml").read_text(encoding="utf-8")
        for field in re.split(r"^    \w+:\n", source, flags=re.MULTILINE)[1:]:
            if "        select:\n" not in field:
                continue
            with self.subTest(field=field):
                keys = re.findall(r"^          translation_key: (\w+)$", field, re.MULTILINE)
                self.assertEqual(len(keys), 1)
                choices = {
                    value.strip('"\'')
                    for value in re.findall(r"^          - (.+)$", field, re.MULTILINE)
                }
                self.assertTrue(choices)
                for language, catalog in self.catalogs.items():
                    self.assertEqual(choices, set(catalog["selector"][keys[0]]["options"]), language)

    def test_native_service_errors_have_translated_messages(self):
        tree = ast.parse((COMPONENT / "switch.py").read_text(encoding="utf-8"))
        keys = {
            node.value.value
            for node in ast.walk(tree)
            if isinstance(node, ast.keyword)
            and node.arg == "translation_key"
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
            and node.value.value.startswith("service_")
        }
        self.assertTrue(keys)
        for language, catalog in self.catalogs.items():
            self.assertTrue(keys <= set(catalog["exceptions"]), language)

    def test_profile_actions_and_switch_controls_have_localized_names(self):
        for language, catalog in self.catalogs.items():
            self.assertEqual(set(catalog["selector"]["profile_action"]["options"]), {"new"})
            self.assertEqual(
                set(catalog["entity"]["switch"]),
                {"circadian_pro", "adapt_brightness", "adapt_color", "sleep_mode"},
                language,
            )


if __name__ == "__main__":
    unittest.main()
