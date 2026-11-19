"""Private, daily PostgreSQL archive backup. Run by the installed systemd unit."""

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import tempfile
import time
from uuid import uuid4

RETENTION_SECONDS = 7 * 24 * 60 * 60
ARCHIVE_NAME = re.compile(r"dealnaru-\d{8}T\d{6}Z-[0-9a-f]{8}\.dump(?:\.sha256)?")
UPDATE_DIRECTORY = re.compile(r"\d{8}-\d{6}-\d{6}")


def prune_backups(backup_dir, update_dir, *, now=None):
    cutoff = (time.time() if now is None else now) - RETENTION_SECONDS
    for path in backup_dir.iterdir():
        if ARCHIVE_NAME.fullmatch(path.name) and path.is_file() and not path.is_symlink() and path.stat().st_mtime < cutoff:
            path.unlink()
    if update_dir.is_dir() and not update_dir.is_symlink():
        for path in update_dir.iterdir():
            if UPDATE_DIRECTORY.fullmatch(path.name) and path.is_dir() and not path.is_symlink() and path.stat().st_mtime < cutoff:
                shutil.rmtree(path)


def run_backup(project, backup_dir, update_dir, owner, *, lock_dir=Path('/run/dealnaru-backup')):
    os.umask(0o077)
    project = project.resolve()
    if not (project / 'compose.yaml').is_file() or not (project / '.env').is_file():
        raise RuntimeError('PROJECT_OR_ENV_NOT_FOUND')
    if backup_dir.is_symlink():
        raise RuntimeError('BACKUP_DIRECTORY_MUST_NOT_BE_A_SYMLINK')
    account = pwd.getpwnam(owner)
    backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    backup_dir.chmod(0o700)
    os.chown(backup_dir, account.pw_uid, account.pw_gid)
    lock_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (lock_dir / 'lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('BACKUP_ALREADY_RUNNING')
            return None
        for path in backup_dir.glob('.partial-*.dump'):
            if path.is_file() and not path.is_symlink() and path.stat().st_mtime < time.time() - 86400:
                path.unlink()
        descriptor, name = tempfile.mkstemp(prefix='.partial-', suffix='.dump', dir=backup_dir)
        partial = Path(name)
        command = ['/usr/bin/docker', 'compose', 'exec', '-T', 'db']
        try:
            with os.fdopen(descriptor, 'wb') as output:
                subprocess.run(command + ['pg_dump', '-U', 'postgres', '-Fc', 'dealnaru'], cwd=project, stdout=output, check=True)
            with partial.open('rb') as source:
                # Without --dbname this only decodes the archive to /dev/null.
                # It never restores into, drops, or changes the live database.
                subprocess.run(command + ['pg_restore', '--no-owner', '--no-privileges', '--file=/dev/null'], cwd=project, stdin=source, stdout=subprocess.DEVNULL, check=True)
            digest = hashlib.sha256()
            with partial.open('rb') as source:
                for block in iter(lambda: source.read(1024 * 1024), b''):
                    digest.update(block)
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
            target = backup_dir / f'dealnaru-{stamp}-{uuid4().hex[:8]}.dump'
            os.chown(partial, account.pw_uid, account.pw_gid)
            partial.chmod(0o600)
            os.replace(partial, target)
            checksum = target.with_suffix('.dump.sha256')
            checksum.write_text(f'{digest.hexdigest()}  {target.name}\n', encoding='ascii')
            checksum.chmod(0o600)
            os.chown(checksum, account.pw_uid, account.pw_gid)
            prune_backups(backup_dir, update_dir)
            print(f'BACKUP_OK={target.name}', flush=True)
            print('ARCHIVE_READ_CHECK_OK', flush=True)
            return target
        finally:
            partial.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--backup-dir', type=Path, required=True)
    parser.add_argument('--update-backup-dir', type=Path, required=True)
    parser.add_argument('--owner', default='ubuntu')
    args = parser.parse_args()
    run_backup(args.project, args.backup_dir, args.update_backup_dir, args.owner)


if __name__ == '__main__':
    main()
