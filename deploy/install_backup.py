"""Install the DealNaru backup job and publish the actual retention settings."""

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile

NOTICE = (
    '정기 DB 백업은 동일한 서울 서버의 접근 제한 폴더에 매일 생성합니다. '
    '정상 백업 완료 후 7일 이전의 정기 백업과 서버의 업데이트 복구용 백업을 자동 정리합니다. '
    '회원 탈퇴 시 운영 DB의 회원·찜·알림·세션 정보를 삭제하며, '
    '탈퇴 전 생성된 백업에 남은 정보는 해당 백업 정리 시 함께 파기합니다.'
)


def unit_files(project, home):
    # These are native systemd arguments, not shell commands.
    def quoted(path):
        return '"' + str(path).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'
    service = f'''[Unit]
Description=DealNaru daily database backup
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
UMask=0077
RuntimeDirectory=dealnaru-backup
RuntimeDirectoryMode=0700
TimeoutStartSec=15min
ExecStart=/usr/bin/python3 /usr/local/lib/dealnaru/backup_job.py --project {quoted(project)} --backup-dir {quoted(home / 'dealnaru-backups')} --update-backup-dir {quoted(home / 'dealnaru-update-backups')} --owner ubuntu
'''
    timer = '''[Unit]
Description=DealNaru backup at 04:00 Korea time

[Timer]
OnCalendar=*-*-* 04:00:00 Asia/Seoul
Persistent=true
AccuracySec=1min

[Install]
WantedBy=timers.target
'''
    return service, timer


def update_notice(env):
    previous = env.read_bytes()
    backup = env.with_name('.env.before-scheduled-backups')
    if not backup.exists():
        backup.write_bytes(previous)
        backup.chmod(0o600)
    lines = previous.splitlines(keepends=True)
    kept = b''.join(line for line in lines if not line.startswith(b'VITE_RETENTION_NOTICE='))
    if kept and not kept.endswith(b'\n'):
        kept += b'\n'
    temporary = env.with_name('.env.backup-setup-tmp')
    temporary.write_bytes(kept + ("VITE_RETENTION_NOTICE='" + NOTICE + "'\n").encode('utf-8'))
    temporary.chmod(0o600)
    os.replace(temporary, env)


def install(project, *, expected_home=Path('/home/ubuntu')):
    project = project.resolve()
    home = project.parent.parent
    if home != expected_home or not (project / '.env').is_file() or not (project / 'compose.yaml').is_file():
        raise RuntimeError('EXPECTED_PROJECT: /home/ubuntu/dealnaru-release/dealnaru')
    source = Path(__file__).resolve().parent
    job = source / 'backup_job.py'
    if not job.is_file():
        raise RuntimeError('BACKUP_JOB_FILE_MISSING')
    subprocess.run(['sudo', '/usr/bin/docker', 'compose', 'config', '--quiet'], cwd=project, check=True)
    service, timer = unit_files(project, home)
    with tempfile.TemporaryDirectory(prefix='dealnaru-backup-setup-') as directory:
        directory = Path(directory)
        service_path = directory / 'dealnaru-backup.service'
        timer_path = directory / 'dealnaru-backup.timer'
        service_path.write_text(service, encoding='ascii')
        timer_path.write_text(timer, encoding='ascii')
        subprocess.run(['sudo', 'install', '-d', '-m', '755', '/usr/local/lib/dealnaru'], check=True)
        subprocess.run(['sudo', 'install', '-o', 'root', '-g', 'root', '-m', '644', str(job), '/usr/local/lib/dealnaru/backup_job.py'], check=True)
        subprocess.run(['sudo', 'install', '-o', 'root', '-g', 'root', '-m', '644', str(service_path), '/etc/systemd/system/dealnaru-backup.service'], check=True)
        subprocess.run(['sudo', 'install', '-o', 'root', '-g', 'root', '-m', '644', str(timer_path), '/etc/systemd/system/dealnaru-backup.timer'], check=True)
    subprocess.run(['sudo', 'systemctl', 'daemon-reload'], check=True)
    subprocess.run(['sudo', 'systemctl', 'start', 'dealnaru-backup.service'], check=True)
    print('FIRST_BACKUP_OK', flush=True)
    subprocess.run(['sudo', 'systemctl', 'enable', '--now', 'dealnaru-backup.timer'], check=True)
    subprocess.run(['sudo', 'systemctl', 'is-active', '--quiet', 'dealnaru-backup.timer'], check=True)
    update_notice(project / '.env')
    print('PUBLIC_BACKUP_NOTICE_UPDATED', flush=True)
    subprocess.run(['sudo', '/usr/bin/docker', 'compose', 'up', '-d', '--build', '--wait', 'web'], cwd=project, check=True)
    subprocess.run(['systemctl', 'list-timers', '--all', 'dealnaru-backup.timer', '--no-pager'], check=True)
    print('BACKUP_SETUP_OK', flush=True)
    print('SCHEDULE=04:00 Asia/Seoul; RETENTION=7 days after successful backup', flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', type=Path, default=Path('/home/ubuntu/dealnaru-release/dealnaru'))
    args = parser.parse_args()
    try:
        if os.geteuid() == 0:
            raise RuntimeError('RUN_AS_UBUNTU_WITHOUT_SUDO')
        install(args.project)
        return 0
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f'BACKUP_SETUP_STOPPED: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
