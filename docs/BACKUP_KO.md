# 운영 DB 정기 백업

설치 명령은 `python3 deploy/install_backup.py`입니다. Lightsail의 ubuntu 계정에서 실행합니다. 설치 도구가 필요한 시스템 파일 설치에만 sudo를 사용하므로 명령 앞에 sudo를 붙이지 않습니다.

- 한국 시간 매일 새벽 4시에 DB 전체를 백업합니다. 서버가 꺼져 놓친 예약은 다시 켜졌을 때 보충합니다.
- 백업에는 게임·가격 이력과 회원·찜·알림 등의 실제 운영 데이터가 포함됩니다.
- `/home/ubuntu/dealnaru-backups`에 보관하며 폴더는 700, 파일은 600 권한입니다. 비밀키를 스크립트에 적거나 출력하지 않습니다.
- 생성한 덤프를 pg_restore로 끝까지 읽어 확인합니다. 이것은 읽기 검사이며 별도 DB에 실제 복원하는 시험과는 다릅니다. 운영 DB를 초기화하거나 덮어쓰지 않습니다.
- 정상 백업 후 7일 이전의 관리 대상 정기 백업과 업데이트 도구가 만든 `dealnaru-update-backups/날짜` 폴더를 정리합니다. PC의 원본 백업이나 서버 이전용 SQL은 이 정리 대상이 아닙니다.
- 실패한 덤프는 최종 백업으로 올리지 않으며 실패 시 이전 정상 백업을 정리하지 않습니다.
- 처음 설치할 때 즉시 한 번 백업하고 예약을 켠 다음, 개인정보 처리방침의 실제 백업 안내를 수정하고 웹을 재빌드합니다.

확인 명령:

```bash
systemctl list-timers --all dealnaru-backup.timer --no-pager
sudo journalctl -u dealnaru-backup.service -n 15 --no-pager
ls -lh ~/dealnaru-backups
```

정상 종료하면 `BACKUP_SETUP_OK`가 나옵니다. 서비스 자체가 백업 후 inactive로 돌아가는 것은 oneshot 방식의 정상 동작입니다. timer는 active 상태를 유지합니다. 목록의 시간은 서버 시간대(UTC)로 표시될 수 있으나 예약은 Asia/Seoul의 04:00입니다.

수동으로 추가 백업:

```bash
sudo systemctl start dealnaru-backup.service
```

예약 중지:

```bash
sudo systemctl disable --now dealnaru-backup.timer
```

백업은 우선 동일한 Lightsail 서버에 저장합니다. 별도 보관본이 필요하면 검증한 덤프와 SHA-256 파일을 PC 등 서버 밖으로 복사합니다. 내려받은 운영 DB 백업에는 개인정보가 있으므로 GitHub·공개 웹 디렉터리·채팅에 올리지 않습니다. .env와 SSH 키도 따로 안전하게 보관해야 합니다.
