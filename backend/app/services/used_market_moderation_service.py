"""관리자가 삭제한 키워드/매물의 재노출과 재수집을 막습니다."""
from hashlib import sha256

from sqlalchemy import and_, exists, func, or_
from sqlalchemy.exc import IntegrityError

from app.models import CrawlingLog, UsedMarketExclusion, UsedMarketItem
from app.utils.time import utc_now


class ExcludedMarketItem(ValueError):
    pass


def keyword_key(value):
    return " ".join(str(value or "").split()).lower()


def visible_market_clause():
    return ~exists().where(and_(
        UsedMarketExclusion.source == UsedMarketItem.source,
        or_(
            and_(UsedMarketExclusion.kind == "keyword", UsedMarketExclusion.value == func.lower(UsedMarketItem.keyword)),
            and_(UsedMarketExclusion.kind == "item", UsedMarketExclusion.value == UsedMarketItem.item_url),
        ),
    ))


def is_excluded(db, *, keyword=None, item_url=None):
    conditions = []
    if keyword is not None:
        conditions.append(and_(UsedMarketExclusion.kind == "keyword", UsedMarketExclusion.value == keyword_key(keyword)))
    if item_url:
        conditions.append(and_(UsedMarketExclusion.kind == "item", UsedMarketExclusion.value == item_url))
    return bool(conditions and db.query(UsedMarketExclusion.id).filter(
        UsedMarketExclusion.source == "중고나라", or_(*conditions),
    ).first())


def exclude(db, *, kind, value, label, admin_id):
    value = keyword_key(value) if kind == "keyword" else value
    key = sha256(f"중고나라:{kind}:{value}".encode()).hexdigest()
    row = db.query(UsedMarketExclusion).filter_by(key=key).first()
    if row is None:
        row = UsedMarketExclusion(key=key, kind=kind, value=value, label=label[:255], created_by_id=admin_id)
        try:
            with db.begin_nested():
                db.add(row)
                db.flush()
        except IntegrityError:
            row = db.query(UsedMarketExclusion).filter_by(key=key).one()
    return row


def delete_keyword(db, keyword, *, admin_id):
    key = keyword_key(keyword)
    if not key or key == "전체":
        raise ValueError("전체 탭은 삭제할 수 없습니다. 삭제할 키워드를 선택해 주세요.")
    exclude(db, kind="keyword", value=key, label=keyword.strip(), admin_id=admin_id)
    count = db.query(UsedMarketItem).filter(
        UsedMarketItem.source == "중고나라", func.lower(UsedMarketItem.keyword) == key,
    ).delete(synchronize_session=False)
    db.add(CrawlingLog(status="SUCCESS", end_time=utc_now(), rows_count=count,
                      message=f"관리자 중고 키워드 삭제: {keyword.strip()} · {count}개 삭제 · 재수집 제외"))
    db.flush()
    return count


def delete_item(db, item, *, admin_id):
    exclude(db, kind="item", value=item.item_url, label=item.title, admin_id=admin_id)
    db.delete(item)
    db.add(CrawlingLog(status="SUCCESS", end_time=utc_now(), rows_count=1, message="관리자 중고 매물 삭제 · 재수집 제외"))
    db.flush()
