from sqlalchemy import String, JSON, Double, Integer, Boolean, ForeignKey, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base


class UploadSession(Base):
    """分段上传会话：整批记录按片接入，收齐并校验通过后才生成 Dataset。"""
    __tablename__ = 'upload_sessions'
    id: Mapped[int] = mapped_column(primary_key=True)
    # 客户端生成的幂等键，防止创建请求超时重试产生多个会话
    client_token: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    source: Mapped[str] = mapped_column(String(200), default='manual.json')
    total_chunks: Mapped[int] = mapped_column(Integer)
    total_records: Mapped[int] = mapped_column(Integer)
    # 客户端对整批记录（最终拼接顺序）计算的 SHA256，完成确认时服务端复核
    batch_checksum: Mapped[str] = mapped_column(String(64))
    # open 进行中 / completed 已生成数据集 / failed 校验失败不可用 / closed 手工放弃
    status: Mapped[str] = mapped_column(String(20), default='open', index=True)
    received_chunks: Mapped[int] = mapped_column(Integer, default=0)
    dataset_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[float] = mapped_column(Double)
    updated_at: Mapped[float] = mapped_column(Double)
    completed_at: Mapped[float | None] = mapped_column(Double, nullable=True)


class UploadChunk(Base):
    """单个分片：同片号重试不重复接收；同片号内容冲突保留原片并报错。"""
    __tablename__ = 'upload_chunks'
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey('upload_sessions.id', ondelete='CASCADE'), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    records: Mapped[list] = mapped_column(JSON)
    record_count: Mapped[int] = mapped_column(Integer)
    checksum: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[float] = mapped_column(Double)
    __table_args__ = (
        UniqueConstraint('session_id', 'chunk_index', name='uq_upload_chunk_index'),
        Index('ix_upload_chunk_session', 'session_id', 'chunk_index'),
    )
