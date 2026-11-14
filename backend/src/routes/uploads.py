from fastapi import APIRouter, Depends, HTTPException
from src.config.database import get_db
from src.middlewares.auth import current_user, can_write
from src.schemas.upload import UploadSessionInput, UploadChunkInput, UploadCompleteInput
from src.services import uploads
from src.services.audit import record

router = APIRouter(prefix='/uploads')


@router.get('')
def listing(db=Depends(get_db), user=Depends(current_user)):
    """上传会话列表（含失败原因），供页面恢复续传与排查。"""
    return [uploads.session_dict(db, s) for s in uploads.list_sessions(db)]


@router.post('', status_code=201)
def create(data: UploadSessionInput, db=Depends(get_db), user=Depends(can_write)):
    s = uploads.create_session(db, data, user)
    if s.received_chunks == 0:
        record(db, user.email, 'upload.session_create',
               {'id': s.id, 'total_chunks': s.total_chunks, 'total_records': s.total_records})
    db.commit()
    return uploads.session_dict(db, s)


@router.get('/{session_id}')
def detail(session_id: int, db=Depends(get_db), user=Depends(current_user)):
    """查缺片：重启/刷新后凭会话 ID 拉取已收片号与缺失片号。"""
    s = uploads.get_session(db, session_id)
    return uploads.session_dict(db, s)


@router.put('/{session_id}/chunks/{chunk_index}')
def put_chunk(session_id: int, chunk_index: int, data: UploadChunkInput,
              db=Depends(get_db), user=Depends(can_write)):
    if data.chunk_index != chunk_index:
        raise HTTPException(422, '路径片号与请求体片号不一致')
    s, accepted, _chunk = uploads.put_chunk(db, session_id, data)
    record(db, user.email,
           'upload.chunk_accept' if accepted else 'upload.chunk_duplicate',
           {'session_id': session_id, 'chunk_index': chunk_index})
    db.commit()
    return uploads.session_dict(db, s)


@router.post('/{session_id}/complete')
def complete(session_id: int, data: UploadCompleteInput | None = None,
             db=Depends(get_db), user=Depends(can_write)):
    """完成确认；可与最后一片同时提交（final_chunk），整体只生成一个完整批次。"""
    payload = data or UploadCompleteInput()
    current = uploads.get_session(db, session_id)
    # 完成确认超时重试：已完成会话直接幂等返回，即便重试体里仍带 final_chunk
    if current.status == 'completed':
        return uploads.session_dict(db, current)
    if payload.final_chunk is not None:
        if payload.final_chunk.chunk_index != current.total_chunks - 1:
            raise HTTPException(422, 'final_chunk 必须是最后一片')
        s, _accepted, _ = uploads.put_chunk(db, session_id, payload.final_chunk, auto_complete=True)
    else:
        s = uploads.finalize(db, current)
    if s.status == 'completed':
        record(db, user.email, 'upload.complete',
               {'session_id': session_id, 'dataset_id': s.dataset_id, 'rows': s.total_records})
    else:
        record(db, user.email, 'upload.failed',
               {'session_id': session_id, 'code': s.error_code, 'message': s.error_message})
    db.commit()
    return uploads.session_dict(db, s)


@router.post('/{session_id}/close')
def close(session_id: int, db=Depends(get_db), user=Depends(can_write)):
    s = uploads.close_session(db, session_id)
    record(db, user.email, 'upload.close', {'session_id': session_id, 'status': s.status})
    db.commit()
    return uploads.session_dict(db, s)
