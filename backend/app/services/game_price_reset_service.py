"""게임과 사용자 연결은 보존하고, 디지털 게임 가격과 파생 캐시만 초기화합니다."""

from app.models import Game, GamePriceTest, PredictionResult, PriceSnapshot, UsedMarketSummary
from app.utils.time import utc_now


def reset_game_prices(db, apply=False):
    result = {
        "price_snapshots": db.query(PriceSnapshot).count(),
        "prediction_cache": db.query(PredictionResult).count(),
        "games_kept": db.query(Game).count(),
        "applied": apply,
    }
    if not apply:
        return result
    # 동일 게임의 진행 중 쓰기가 끝난 뒤 초기화합니다. CLI 실행 시 서버도 중지해야 합니다.
    db.query(Game.id).order_by(Game.id).with_for_update().all()
    db.query(GamePriceTest).filter(GamePriceTest.active.is_(True)).update(
        {GamePriceTest.active: False, GamePriceTest.ended_at: utc_now()}, synchronize_session=False,
    )
    db.query(PredictionResult).delete(synchronize_session=False)
    db.query(PriceSnapshot).delete(synchronize_session=False)
    db.query(Game).update(
        {
            Game.history_synced_at: None,
            Game.history_attempted_at: None,
            Game.history_start_at: None,
            Game.history_error: None,
            Game.history_retry_at: None,
            Game.history_queued_at: None,
            Game.history_priority: 0,
            Game.history_lease_token: None,
            Game.history_lease_until: None,
        },
        synchronize_session=False,
    )
    db.query(Game).filter(Game.itad_id.is_(None)).update(
        {Game.itad_lookup_checked_at: None}, synchronize_session=False
    )
    db.query(UsedMarketSummary).update(
        {
            UsedMarketSummary.digital_lowest: 0,
            UsedMarketSummary.digital_discount_rate: 0,
        },
        synchronize_session=False,
    )
    db.flush()
    return result
