"""Schema pinning and streaming validation must fail closed on damaged inputs."""
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('ctml_project', ROOT / 'tools/ctml_project.py')
project = importlib.util.module_from_spec(spec)
spec.loader.exec_module(project)


class ProjectValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        shutil.copytree(ROOT / 'xsd', self.root / 'xsd')
        (self.root / 'provenance').mkdir()
        (self.root / 'provenance/schema-lock.json').write_text(
            json.dumps({'files': project.schema_files(self.root)}), encoding='utf-8')

    def test_current_document_and_invalid_scalar(self):
        valid = self.root / 'valid.xml'
        valid.write_bytes((ROOT / 'samples/sample-tournament.xml').read_bytes())
        invalid = self.root / 'invalid.xml'
        invalid.write_text('<ctml:tournament xmlns:ctml="urn:ctml:2.0" ctmlVersion="2.1" id="bad"><ctml:header><ctml:rounds>not-a-number</ctml:rounds></ctml:header></ctml:tournament>', encoding='utf-8')
        report = project.validate_files(self.root, [valid, invalid])
        self.assertEqual(report['checked'], 2)
        self.assertEqual(report['valid'], 1)
        self.assertTrue(report['files'][0]['valid'])
        self.assertFalse(report['files'][1]['valid'])

    def test_changed_or_missing_schema_refuses_validation(self):
        schema = self.root / 'xsd/ctml-core.xsd'
        schema.write_bytes(schema.read_bytes() + b'\n')
        report = project.validate_files(self.root, [ROOT / 'samples/sample-tournament.xml'])
        self.assertEqual(report['checked'], 0)
        self.assertIn('ctml-core.xsd', report['schema_errors'][0])

    def test_import_hash_detects_changed_data(self):
        path = self.root / 'data.xml'
        path.write_text('original', encoding='utf-8')
        (self.root / 'provenance/consolidation-import.json').write_text(json.dumps(
            {'files': [{'destination': 'data.xml', 'source_sha256': project.sha256(path)}]}), encoding='utf-8')
        self.assertEqual(project.verify_import(self.root)['failures'], [])
        path.write_text('changed', encoding='utf-8')
        self.assertEqual(project.verify_import(self.root)['failures'], ['data.xml'])


if __name__ == '__main__':
    unittest.main()
