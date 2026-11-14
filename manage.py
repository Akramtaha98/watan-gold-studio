"""Local operational commands; no credentials are printed."""
import argparse
import os
import sqlite3
from pathlib import Path

from storage import Storage


def main():
    parser=argparse.ArgumentParser()
    sub=parser.add_subparsers(dest='command',required=True)
    backup=sub.add_parser('backup',help='Consistent SQLite backup, including committed WAL data')
    backup.add_argument('--output',required=True)
    sub.add_parser('restore-releases',help='Restore filesystem mirrors from verified DB snapshots')
    args=parser.parse_args()
    store=Storage()
    if args.command=='backup':
        if store.engine.dialect.name!='sqlite':raise SystemExit('Use pg_dump / the managed PostgreSQL backup service for this database.')
        destination=Path(args.output).resolve()
        if destination.exists():raise SystemExit('Backup output already exists; choose a new path.')
        if destination==Path(store.engine.url.database).resolve():raise SystemExit('Backup must not replace the live database.')
        destination.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        with sqlite3.connect(store.engine.url.database) as source, sqlite3.connect(destination) as target:
            source.backup(target)
        destination.chmod(0o600)
        print('Consistent SQLite backup created. Keep it private and test restoration separately.')
    else:
        for release in store.list_releases():store.materialize_release(release['key'],store.release_files(release['key']))
        print('Release mirrors restored from checksum-verified database archives.')
    store.close()


if __name__=='__main__':main()
