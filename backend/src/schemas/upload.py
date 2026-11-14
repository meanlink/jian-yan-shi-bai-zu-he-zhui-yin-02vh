from pydantic import BaseModel, Field


class UploadSessionInput(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    source: str = Field(default='manual.json', max_length=200)
    total_chunks: int = Field(ge=1, le=2000)
    total_records: int = Field(ge=5, le=5000)
    batch_checksum: str = Field(min_length=64, max_length=64)
    # 客户端生成的幂等键（UUID），创建超时重试时复用同一键
    client_token: str = Field(min_length=8, max_length=64)


class UploadChunkInput(BaseModel):
    chunk_index: int = Field(ge=0)
    records: list[dict] = Field(min_length=1, max=5000)
    checksum: str = Field(min_length=64, max_length=64)


class UploadCompleteInput(BaseModel):
    # 允许携带最后一片同时提交；为空时仅做完成确认
    final_chunk: 'UploadChunkInput | None' = None


UploadCompleteInput.model_rebuild()
