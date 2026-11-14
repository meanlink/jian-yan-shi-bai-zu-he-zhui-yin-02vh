"""分段续传服务。

设计要点：
- 会话(UploadSession)描述整批接入意图，分片(UploadChunk)按片号唯一落库；
- 同片号重试：校验和一致直接幂等返回，不重复接收；内容冲突保留原片并 409；
- 完成确认与“最后一片同时提交”走同一 finalize 路径，行锁 + 状态机保证只生成一个批次；
- 缺片、总记录数不符、整批指纹不符或字段校验失败：会话置 failed，不生成 Dataset；
- 只有 completed 会话产生的 Dataset 才对分析开放。
"""
import json
import math
import time

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.models import Dataset, UploadSession, UploadChunk
from src.services.business import metadata
from src.utils.serialization import checksum

# 单片 JSON 体积上限（字节）；低于 Nginx client_max_body_size(8MB) 以留出请求开销余量
MAX_CHUNK_BYTES = 6 * 1024 * 1024


def _now():
    return time.time()


def _lock_session(db, session_id: int) -> UploadSession:
    """读取会话并加行锁；SQLite 不支持 FOR UPDATE 时退化为普通查询。"""
    stmt = select(UploadSession).where(UploadSession.id == session_id).with_for_update()
    try:
        session = db.scalar(stmt)
    except Exception:
        session = db.get(UploadSession, session_id)
    if session is None:
        raise HTTPException(404, '上传会话不存在，可能已被清理，请重新创建')
    return session


def session_dict(db, s: UploadSession):
    received = list(db.scalars(
        select(UploadChunk.chunk_index).where(UploadChunk.session_id == s.id)
    ))
    missing = sorted(set(range(s.total_chunks)) - set(received))
    return {
        'id': s.id,
        'client_token': s.client_token,
        'name': s.name,
        'source': s.source,
        'total_chunks': s.total_chunks,
        'total_records': s.total_records,
        'batch_checksum': s.batch_checksum,
        'status': s.status,
        'received_chunks': s.received_chunks,
        'received_indexes': sorted(received),
        'missing_indexes': missing,
        'dataset_id': s.dataset_id,
        'error_code': s.error_code,
        'error_message': s.error_message,
        'created_at': s.created_at,
        'updated_at': s.updated_at,
        'completed_at': s.completed_at,
    }


def create_session(db, data, user) -> UploadSession:
    name = data.name.strip()
    if len(name) < 2:
        raise HTTPException(422, '批次名称去除空格后至少需要两个字符')
    # 幂等：同一 client_token 超时重试返回原会话，不重复创建
    existing = db.scalar(select(UploadSession).where(UploadSession.client_token == data.client_token))
    if existing:
        return existing
    if db.scalar(select(Dataset).where(Dataset.name == name)):
        raise HTTPException(409, '该数据批次名已存在，请修改名称')
    if db.scalar(select(UploadSession).where(
            UploadSession.name == name, UploadSession.status == 'open')):
        raise HTTPException(409, '同名上传会话正在进行中，请继续续传或更换名称')
    now = _now()
    session = UploadSession(
        client_token=data.client_token, name=name, source=data.source,
        total_chunks=data.total_chunks, total_records=data.total_records,
        batch_checksum=data.batch_checksum.lower(), status='open',
        received_chunks=0, created_by=user.id, created_at=now, updated_at=now,
    )
    db.add(session)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raced = db.scalar(select(UploadSession).where(UploadSession.client_token == data.client_token))
        if raced:
            return raced
        raise HTTPException(409, '上传会话创建冲突，请重试')
    return session


def list_sessions(db):
    return db.scalars(
        select(UploadSession).order_by(UploadSession.id.desc()).limit(100)
    ).all()


def get_session(db, session_id: int) -> UploadSession:
    return _lock_session(db, session_id)


def _validate_open(session: UploadSession):
    if session.status == 'completed':
        raise HTTPException(409, '该会话已完成并生成数据批次，无需重复上传')
    if session.status == 'failed':
        raise HTTPException(409, f'会话已失败且不可续传：{session.error_message or "校验未通过"}，请重新创建会话')
    if session.status == 'closed':
        raise HTTPException(409, '会话已关闭，请重新创建上传会话')


def _validate_records(records: list[dict]) -> list[dict]:
    """按业务字段口径校验并返回规范化副本，不修改客户端原始片内容。

    原始片用于传输指纹核对；规范化结果（数值列转 float）用于生成 Dataset，
    与整包接入的存储口径保持一致。
    """
    columns = metadata()['columns']
    normalized = []
    for index, raw in enumerate(records):
        row = dict(raw)
        for c in columns:
            value = row.get(c['key'])
            if value is None or value == '':
                raise HTTPException(422, f'片内第 {index + 1} 行缺少字段 {c["label"]}')
            if c['type'] == 'number':
                try:
                    if isinstance(value, bool) or not math.isfinite(float(value)):
                        raise ValueError()
                    row[c['key']] = float(value)
                except (TypeError, ValueError, OverflowError):
                    raise HTTPException(422, f'片内第 {index + 1} 行 {c["label"]} 必须为有限数值')
        normalized.append(row)
    return normalized


def put_chunk(db, session_id: int, data, *, auto_complete: bool = False):
    """接收一片。返回 (session, accepted_bool, chunk_or_none)。"""
    if data.chunk_index >= (session := _lock_session(db, session_id)).total_chunks:
        raise HTTPException(422, f'片号 {data.chunk_index} 超出声明范围 0~{session.total_chunks - 1}')
    _validate_open(session)

    records = data.records
    # 传输校验和必须基于客户端原始内容计算；字段规范化（int->float）发生在其后
    actual_checksum = checksum(records)
    if actual_checksum != data.checksum.lower():
        raise HTTPException(422, '分片校验和与内容不一致，请重新生成本片后上传')
    # 仅校验，不修改 records：存储的片内容保持客户端原样以支持整批指纹复核
    _validate_records(records)

    existing = db.scalar(select(UploadChunk).where(
        UploadChunk.session_id == session_id,
        UploadChunk.chunk_index == data.chunk_index,
    ))
    accepted = False
    if existing is not None:
        # 同片号重试：内容一致 -> 幂等，不重复接收；冲突 -> 保留原片并报错
        if existing.checksum != actual_checksum:
            raise HTTPException(
                409,
                f'片号 {data.chunk_index} 已存在不同内容，服务端保留首次接收的原片；'
                '请勿更改片内容后重传，若需调整请重建上传会话',
            )
        chunk = existing
    else:
        body = json.dumps(records, ensure_ascii=False, allow_nan=False).encode()
        if len(body) > MAX_CHUNK_BYTES:
            raise HTTPException(413, f'单片体积超过 {MAX_CHUNK_BYT // 1024 // 1024}MB 上限，请减小分片粒度')
        chunk = UploadChunk(
            session_id=session_id, chunk_index=data.chunk_index, records=records,
            record_count=len(records), checksum=actual_checksum,
            size_bytes=len(body), created_at=_now(),
        )
        db.add(chunk)
        session.received_chunks += 1
        session.updated_at = _now()
        accepted = True
        db.flush()

    if auto_complete:
        finalize(db, session)
    return session, accepted, chunk


def _mark_failed(db, session: UploadSession, code: str, message: str):
    # 并发完成时，已生成批次的会话不允许被晚到的失败分支覆盖
    if session.status == 'completed':
        return
    session.status = 'failed'
    session.error_code = code
    session.error_message = message[:500]
    session.updated_at = _now()
    db.flush()


def finalize(db, session: UploadSession) -> UploadSession:
    """收齐核对并生成 Dataset；幂等：重复/并发完成只产生一个完整批次。"""
    if session.status == 'completed':
        return session
    _validate_open(session)

    chunks = list(db.scalars(
        select(UploadChunk).where(UploadChunk.session_id == session.id).order_by(UploadChunk.chunk_index)
    ))
    received_indexes = {c.chunk_index for c in chunks}
    missing = sorted(set(range(session.total_chunks)) - received_indexes)
    if missing:
        _mark_failed(db, session, 'MISSING_CHUNKS',
                     f'仍缺少 {len(missing)} 个分片（片号：{missing[:20]}），批次不可用')
        return session

    records = []
    for chunk in chunks:
        records.extend(chunk.records)

    if len(records) != session.total_records:
        _mark_failed(db, session, 'RECORD_COUNT_MISMATCH',
                     f'总记录数核对失败：声明 {session.total_records} 条，实际收齐 {len(records)} 条，批次不可用')
        return session

    actual_checksum = checksum(records)
    if actual_checksum != session.batch_checksum:
        _mark_failed(db, session, 'CHECKSUM_MISMATCH',
                     '整批指纹核对失败：服务端重算 SHA256 与会话声明不一致，批次不可用')
        return session

    # 字段全量复核并规范化（单片上传时已校验，这里兜底历史/异常数据）
    try:
        normalized = _validate_records(records)
    except HTTPException as exc:
        _mark_failed(db, session, 'FIELD_INVALID', str(exc.detail))
        return session

    # 并发完成兜底：另一事务可能已生成同名批次；命中时直接复用，不允许误判失败
    existing_dataset = db.scalar(select(Dataset).where(Dataset.name == session.name))
    if existing_dataset is not None:
        if session.status == 'open':
            session.status = 'completed'
            session.dataset_id = existing_dataset.id
            session.completed_at = _now()
            session.updated_at = session.completed_at
            db.flush()
        return session

    now = _now()
    dataset = Dataset(
        name=session.name, source=session.source, records=normalized,
        checksum=checksum(normalized), created_at=now,
    )
    db.add(dataset)
    try:
        db.flush()
    except IntegrityError:
        # 同名 Dataset 唯一约束的并发兜底：复用已存在批次而非报错
        db.rollback()
        raced = db.scalar(select(Dataset).where(Dataset.name == session.name))
        fresh = db.get(UploadSession, session.id)
        if raced is not None and fresh.status != 'completed':
            fresh.status = 'completed'
            fresh.dataset_id = raced.id
            fresh.completed_at = _now()
            fresh.updated_at = fresh.completed_at
            db.commit()
        return db.get(UploadSession, session.id)
    session.status = 'completed'
    session.dataset_id = dataset.id
    session.completed_at = now
    session.updated_at = now
    session.error_code = None
    session.error_message = None
    db.flush()
    return session


def close_session(db, session_id: int) -> UploadSession:
    session = _lock_session(db, session_id)
    if session.status == 'open':
        session.status = 'closed'
        session.updated_at = _now()
        db.flush()
    return session
