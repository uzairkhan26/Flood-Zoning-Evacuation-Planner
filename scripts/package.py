#!/usr/bin/env python3
"""Build the zip file to install in QGIS / upload to plugins.qgis.org.

Usage:  python3 scripts/package.py      ->  dist/flood_zoning_evacuation_planner-<version>.zip
"""
import configparser
import os
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = 'flood_zoning_evacuation_planner'
EXCLUDE_DIRS = {'__pycache__', '.pytest_cache'}
EXCLUDE_EXT = {'.pyc', '.pyo'}


def main():
    meta = configparser.ConfigParser()
    meta.read(os.path.join(ROOT, PKG, 'metadata.txt'), encoding='utf-8')
    version = meta['general']['version']
    os.makedirs(os.path.join(ROOT, 'dist'), exist_ok=True)
    out = os.path.join(ROOT, 'dist', '%s-%s.zip' % (PKG, version))
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for folder, dirs, files in os.walk(os.path.join(ROOT, PKG)):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            for f in sorted(files):
                if os.path.splitext(f)[1] in EXCLUDE_EXT:
                    continue
                full = os.path.join(folder, f)
                z.write(full, os.path.relpath(full, ROOT))
    print(out)


if __name__ == '__main__':
    main()
