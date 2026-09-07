from __future__ import annotations

import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import build_enriched_ssp as ssp  # noqa: E402


NS2 = "https://purl.org/ctml/2.0/"
NS13 = "https://purl.org/ctml/1.3/"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


class EnrichedSspTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.base = self.root / "base.ssp"
        self.base_bytes = b'@PLAYER "., -_*"\r\nDoe, Jane\r\n  = Jane Doe\r\n'
        self.base.write_bytes(self.base_bytes)
        self.places_dir = self.root / "places"
        write(
            self.places_dir / "places.xml",
            f"""<placeRegistry xmlns="{NS2}">
  <place ref="country:FRA" kind="country"><name>France</name><country>FRA</country></place>
  <place ref="city:paris-fr" kind="city"><name>Paris</name><country>FRA</country><altNames><altName>Paris, France</altName></altNames></place>
  <place ref="city:paris-us" kind="city"><name>Paris</name><country>USA</country></place>
  <place ref="city:vienna" kind="city"><name>Vienna</name><country>AUT</country><altNames><altName>Wien</altName></altNames></place>
</placeRegistry>""",
        )
        self.active_events = self.root / "events.xml"
        write(
            self.active_events,
            f"""<eventRegistry xmlns="{NS2}">
  <eventSeries ref="series:alpha"><name>Alpha Open</name></eventSeries>
  <eventOccurrence ref="event:one"><seriesRef>series:alpha</seriesRef><name>Alpha Open 2020</name><aliases><alias site="Vienna">Alpha 2020</alias></aliases></eventOccurrence>
  <eventOccurrence ref="event:two"><seriesRef>series:alpha</seriesRef><name>Alpha Open 2021</name><aliases><alias site="Paris">Common Alias</alias></aliases></eventOccurrence>
  <eventOccurrence ref="event:three"><seriesRef>series:alpha</seriesRef><name>Other Open</name><aliases><alias>Common Alias</alias><alias>Other #2</alias></aliases></eventOccurrence>
</eventRegistry>""",
        )
        self.edo_events = self.root / "edo-events.xml"
        write(
            self.edo_events,
            f"""<eventRegistry xmlns="{NS13}">
  <eventOccurrence ref="edo:event"><name>Vienna 1900</name><aliases><alias>Wien 1900</alias></aliases><placeRef ref="edo:vienna"><name>Vienna</name></placeRef></eventOccurrence>
</eventRegistry>""",
        )
        self.edo_places = self.root / "edo-places.xml"
        write(
            self.edo_places,
            f"""<placeRegistry xmlns="{NS13}">
  <place ref="edo:vienna" kind="city"><name>Vienna</name><altNames><altName>Vienna, Austria</altName></altNames></place>
</placeRegistry>""",
        )
        self.edo_sites = self.root / "edo-sites.xml"
        write(self.edo_sites, f'<placeRegistry xmlns="{NS13}"/>')
        self.chessmetrics_events = self.root / "chessmetrics-events.xml"
        write(self.chessmetrics_events, f'<eventRegistry xmlns="{NS13}"/>')
        self.output = self.root / "build" / "enriched.ssp"
        self.report = self.root / "build" / "report.json"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def args(self) -> argparse.Namespace:
        return argparse.Namespace(
            base=self.base,
            active_events=self.active_events,
            active_places_dir=self.places_dir,
            edo_events=self.edo_events,
            edo_places=self.edo_places,
            edo_sites=self.edo_sites,
            chessmetrics_events=self.chessmetrics_events,
            output=self.output,
            report=self.report,
            max_round=20,
        )

    def test_build_preserves_base_and_emits_sections_in_order(self) -> None:
        result = ssp.build(self.args())
        output = self.output.read_bytes()
        self.assertTrue(output.startswith(self.base_bytes))
        self.assertEqual(["SITE", "EVENT", "ROUND"], result["generated"]["validation"]["section_markers"])
        self.assertNotIn(b"\n@SITE", self.base_bytes)
        self.assertIn(b'@SITE "., -_()"\r\n', output)
        self.assertIn(b'@EVENT ",. -_"\r\n', output)
        self.assertIn(b'@ROUND ""\r\n', output)
        self.assertIn(b'%Prefix "01. " "1. "\r\n', output)
        self.assertIn(b'Interzonal\r\n  = IZ\r\n', output)
        self.assertNotIn(b"\n", output.replace(b"\r\n", b""))

    def test_unique_sites_are_enriched_but_ambiguous_bare_name_is_omitted(self) -> None:
        ssp.build(self.args())
        generated = self.output.read_text(encoding="utf-8").split(ssp.GENERATED_BANNER, 1)[1]
        self.assertIn("Vienna AUT", generated)
        self.assertIn("  = Wien", generated)
        self.assertNotIn("\n  = Paris\n", generated)
        report = json.loads(self.report.read_text(encoding="utf-8"))
        self.assertGreater(report["diagnostics"]["counts"]["site_ambiguous_place_name"], 0)

    def test_ambiguous_event_alias_is_not_emitted(self) -> None:
        ssp.build(self.args())
        generated = self.output.read_text(encoding="utf-8").split(ssp.GENERATED_BANNER, 1)[1]
        self.assertNotIn("  = Common Alias", generated)
        self.assertNotIn("Other #2", generated)
        report = json.loads(self.report.read_text(encoding="utf-8"))
        counts = report["diagnostics"]["counts"]
        self.assertGreater(counts["event_ambiguous_alias"], 0)
        self.assertGreater(counts["event_unrepresentable_contains_comment_marker"], 0)

    def test_generated_tail_is_deterministic(self) -> None:
        first = ssp.build(self.args())
        first_tail = self.output.read_bytes().split(ssp.GENERATED_BANNER.encode("ascii"), 1)[1]
        second = ssp.build(self.args())
        second_tail = self.output.read_bytes().split(ssp.GENERATED_BANNER.encode("ascii"), 1)[1]
        self.assertEqual(first_tail, second_tail)
        self.assertEqual(first["generated"]["sha256"], second["generated"]["sha256"])

    def test_rejects_base_with_existing_non_player_section(self) -> None:
        self.base.write_bytes(self.base_bytes + b'@SITE "., -_()"\r\n')
        with self.assertRaisesRegex(ValueError, "already contains"):
            ssp.build(self.args())

    def test_refuses_to_overwrite_base(self) -> None:
        args = self.args()
        args.output = self.base
        with self.assertRaisesRegex(ValueError, "must not overwrite"):
            ssp.build(args)
        self.assertEqual(self.base_bytes, self.base.read_bytes())


if __name__ == "__main__":
    unittest.main()
