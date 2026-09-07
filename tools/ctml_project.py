"""Validate the canonical CTML project and its pinned schema bundle.

No imported source, tournament, registry, or schema is rewritten by this tool.
Reports are generated under build/validation, or an explicitly chosen path.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def schema_files(root: Path) -> dict[str, str]:
    return {p.name: sha256(p) for p in sorted((root / 'xsd').glob('*.xsd'))}


def check_schema_lock(root: Path) -> list[str]:
    lock_path = root / 'provenance/schema-lock.json'
    if not lock_path.is_file():
        return [f'Missing schema lock: {lock_path}']
    expected = json.loads(lock_path.read_text(encoding='utf-8'))['files']
    actual = schema_files(root)
    return [f'Schema differs from pinned release: {name}'
            for name in sorted(set(expected) | set(actual))
            if expected.get(name) != actual.get(name)]


def validate_files(root: Path, paths: list[Path]) -> dict:
    from lxml import etree
    errors = check_schema_lock(root)
    if errors:
        return {'schema_errors': errors, 'checked': 0, 'valid': 0, 'files': []}
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    schema = etree.XMLSchema(etree.parse(str(root / 'xsd/ctml.xsd'), parser))
    results = []
    for path in paths:
        entry = {'path': str(path.resolve()), 'valid': False}
        try:
            # Streaming validation keeps the large player shards out of a full DOM.
            # Every element is schema-validated before it is released.
            stream = etree.iterparse(str(path), events=('end',), schema=schema,
                                     no_network=True, resolve_entities=False,
                                     huge_tree=True)
            for _, element in stream:
                if element.getparent() is not None:
                    element.clear()
                    while element.getprevious() is not None:
                        del element.getparent()[0]
            entry['valid'] = True
        except (etree.XMLSyntaxError, OSError) as exc:
            entry['error'] = str(exc)
        results.append(entry)
        if not entry['valid'] or len(results) % 25 == 0 or path.stat().st_size > 20_000_000:
            print(f'{"PASS" if entry["valid"] else "FAIL"} {path} ({len(results)}/{len(paths)})', flush=True)
    return {'schema_errors': [], 'checked': len(results),
            'valid': sum(r['valid'] for r in results), 'files': results,
            'scope': 'XSD validation; does not establish source accuracy or completeness'}


def verify_import(root: Path) -> dict:
    imported = json.loads((root / 'provenance/consolidation-import.json').read_text(encoding='utf-8'))
    adjusted_path = root / 'provenance/consolidation-adjustments.json'
    adjusted = json.loads(adjusted_path.read_text(encoding='utf-8')) if adjusted_path.exists() else {}
    failures = []
    for i, record in enumerate(imported['files'], 1):
        relative = record['destination']
        expected = adjusted.get(relative, {}).get('sha256', record['source_sha256'])
        path = root / relative
        if not path.is_file() or sha256(path) != expected:
            failures.append(relative)
        if i % 50 == 0:
            print(f'Checked {i}/{len(imported["files"])} imported files', flush=True)
    return {'checked': len(imported['files']), 'failures': failures}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--root', type=Path, default=ROOT)
    sub = ap.add_subparsers(dest='command', required=True)
    sub.add_parser('status')
    validate = sub.add_parser('validate')
    validate.add_argument('--registries', action='store_true', help='Also validate every imported registry shard (about 5 GB).')
    validate.add_argument('--samples', action='store_true', help='Also validate the historical sample documents.')
    validate.add_argument('--report', type=Path)
    verify = sub.add_parser('verify-import')
    verify.add_argument('--report', type=Path)
    args = ap.parse_args()
    root = args.root.resolve()
    if args.command == 'status':
        print(json.dumps({'canonical_root': str(root), 'schema_errors': check_schema_lock(root),
                          'tournaments': len(list((root / 'tours').glob('*.ctml'))),
                          'registry_files': len(list((root / 'registry').rglob('*.xml'))),
                          'source_catalog': str(root / 'provenance/sources.json'),
                          'historical_curation': 'Managed upstream in D:/elysium/db/historical_ssp and D:/dev/proj/ssp'}, indent=2))
        return int(bool(check_schema_lock(root)))
    if args.command == 'validate':
        paths = sorted((root / 'tours').glob('*.ctml'))
        if args.samples:
            paths += sorted((root / 'samples').glob('*.xml'))
        if args.registries:
            paths += sorted((root / 'registry').rglob('*.xml'))
        report = validate_files(root, paths)
        failed = bool(report['schema_errors']) or report['checked'] == 0 or report['valid'] != report['checked']
    else:
        report = verify_import(root)
        failed = bool(report['failures'])
    target = args.report or root / 'build/validation' / f'{args.command}.json'
    # Explicit report output must never overwrite canonical inputs or provenance.
    if target.resolve().is_relative_to(root):
        relative = target.resolve().relative_to(root)
        if not relative.parts or relative.parts[0] not in ('build', 'out'):
            ap.error('Reports inside the project must be under build/ or out/.')
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(f'Report: {target}; {"FAILED" if failed else "PASSED"}', flush=True)
    return int(failed)


if __name__ == '__main__':
    sys.exit(main())
