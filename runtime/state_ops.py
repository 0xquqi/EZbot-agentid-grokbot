"""Offline-compatible, private SQLite snapshots and additive schema versioning."""
import os
import sqlite3
from contextlib import closing
from pathlib import Path

VERSION=1


def private_target(path):
    path=Path(path).expanduser().resolve();source=Path(__file__).resolve().parents[1]
    if path==source or source in path.parents:raise ValueError('private backup must be outside source')
    if path.exists():raise FileExistsError('never overwrite a state snapshot')
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
    return path


def snapshot(database,output):
    # Open read-only BEFORE creating an output; a typo must not create an empty DB.
    with closing(sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro',uri=True)) as src:
        if src.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('invalid source database')
        target=private_target(output)
        try:
            with closing(sqlite3.connect(target)) as dst:
                src.backup(dst)
                if dst.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('snapshot integrity failure')
        except Exception:
            target.unlink(missing_ok=True)
            raise
    return str(target)


def restore_copy(backup,new_database):
    # Restore into a NEW location; active source/runtime is never overwritten.
    with closing(sqlite3.connect(Path(backup).resolve().as_uri()+'?mode=ro',uri=True)) as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('invalid backup')
        if db.execute('PRAGMA user_version').fetchone()[0]>VERSION:raise ValueError('newer schema requires newer runtime')
    return snapshot(backup,new_database)


def migrate(database):
    with closing(sqlite3.connect(database)) as db:
        version=db.execute('PRAGMA user_version').fetchone()[0]
        if version>VERSION:raise ValueError('database from newer runtime; refusing downgrade')
        if version==VERSION:return
        has_tables=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' LIMIT 1").fetchone()
        if has_tables:
            import uuid
            snapshot(database,str(database)+'.before-v1-'+str(uuid.uuid4())+'.sqlite')
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS schema_history(version INTEGER PRIMARY KEY, note TEXT)')
            db.execute('INSERT OR IGNORE INTO schema_history VALUES (?,?)',(VERSION,'Additive schemas; preserve orders, jobs, cases and unknown submissions'))
            db.execute('PRAGMA user_version=1')
