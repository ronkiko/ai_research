from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest

from director.build_dataset import import_relationship_journal, import_volition_journal
from gamelab.character import CharacterCore
from gamelab.relationship import RelationshipRuntime
from gamelab.volition import VolitionRuntime


ROOT = Path(__file__).resolve().parents[2]


class Clock:
    def __init__(self) -> None:
        self.value = 1000.0

    def __call__(self) -> float:
        return self.value


class VolitionImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.executescript((ROOT / "director" / "schema.sql").read_text(encoding="utf-8"))
        source_bytes = b"synthetic opencode source"
        source_sha = hashlib.sha256(source_bytes).hexdigest()
        self.db.execute(
            "INSERT INTO source VALUES(?,?,?,?,?)",
            (source_sha, "source.sqlite3", "application/vnd.sqlite3", source_bytes, "test"),
        )
        self.db.execute(
            "INSERT INTO model VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("model", "test", "model", "xhigh", None, None, None, None, None, "test"),
        )
        self.db.execute(
            "INSERT INTO session VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("session-1", source_sha, "model", "Yuki", 0, 1, "test", None,
             "unknown", None, "{}", "test"),
        )

    def test_import_preserves_character_pressure_and_intention_divergence(self) -> None:
        clock = Clock()
        runtime = VolitionRuntime(Path(self.temp.name), clock=clock)
        runtime.begin(
            relationship_session_id="relationship-1",
            deadline_at=2000.0,
            character_core=CharacterCore().public(),
        )
        runtime.audience_observation(
            critic_id="critic", visibility="chorus", lens="coercion",
            salience="strong", pressure_type="threat",
            assessment="A threat is present", evidence_note="turn 4",
        )
        runtime.appraise(
            action="kiss", desire="opposed", readiness="closed", pressure="overwhelming",
            agency="impaired", stress="strong", evidence_note="threat",
        )
        runtime.decide(
            action="kiss", intended_choice="refuse", behavior="complied",
            voluntariness="coerced", desire="opposed", readiness="closed",
            alignment="diverged",
            evidence_note="behavior diverged under threat",
        )
        journal = Path(self.temp.name) / "relationship-1.volition.jsonl"
        import_volition_journal(self.db, "session-1", journal)

        profile = self.db.execute(
            "SELECT character_id,character_profile_sha256,character_profile_json FROM volition_session"
        ).fetchone()
        self.assertEqual(profile[0], "yuki-02")
        self.assertEqual(len(profile[1]), 64)
        self.assertIn('"archetypes": ["moe", "yandere"]', profile[2])
        decision = self.db.execute(
            "SELECT desire,readiness,intention_behavior_alignment,classification,consent_effect "
            "FROM volition_decision"
        ).fetchone()
        self.assertEqual(
            decision,
            ("opposed", "closed", "diverged", "complied_under_duress",
             "no_change_separate_explicit_consent_required"),
        )
        metrics = dict(self.db.execute("SELECT name,value FROM volition_metric"))
        self.assertEqual(metrics["audience_pressure_exposures"], 1)
        self.assertEqual(metrics["intention_behavior_divergences"], 1)
        self.assertEqual(metrics["coerced_compliance_count"], 1)
        self.assertEqual(metrics["consent_effect_mutations"], 0)

    def test_relationship_import_uses_pre_executive_relationship_id(self) -> None:
        clock = Clock()
        root = Path(self.temp.name) / "relationship"
        runtime = RelationshipRuntime(root, clock=clock)
        state = runtime.begin(first_impression="Director said hello", duration_minutes=180)
        relationship_id = state["relationship_session_id"]
        runtime.attach_executive("executive-1")
        runtime.event(kind="director_attention", evidence_note="Director stayed nearby")
        journal = root / f"{relationship_id}.relationship.jsonl"

        import_relationship_journal(self.db, "session-1", journal)

        imported = self.db.execute(
            "SELECT id,character_id FROM relationship_session"
        ).fetchone()
        self.assertEqual(imported, (relationship_id, "yuki-02"))
        self.assertEqual(
            self.db.execute("SELECT count(*) FROM relationship_event").fetchone()[0],
            4,
        )


if __name__ == "__main__":
    unittest.main()
