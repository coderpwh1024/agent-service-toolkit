ALTER TABLE video_generation_tasks ADD COLUMN IF NOT EXISTS model VARCHAR(64);
ALTER TABLE video_generation_tasks ADD COLUMN IF NOT EXISTS first_frame_url VARCHAR(2048);
ALTER TABLE video_generation_tasks ADD COLUMN IF NOT EXISTS last_frame_url VARCHAR(2048);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'video_generation_tasks'::regclass
            AND conname = 'ck_video_generation_tasks_model'
    ) THEN
        ALTER TABLE video_generation_tasks ADD CONSTRAINT ck_video_generation_tasks_model
            CHECK (model IN (
                'happyhorse-1.1-t2v', 'happyhorse-1.0-t2v', 'happyhorse-1.0-video-edit',
                'wan3.0-video', 'wan3.0-video-prime'
            ));
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'video_generation_tasks'::regclass
            AND conname = 'ck_video_generation_tasks_frames'
    ) THEN
        ALTER TABLE video_generation_tasks ADD CONSTRAINT ck_video_generation_tasks_frames
            CHECK (
                (last_frame_url IS NULL OR first_frame_url IS NOT NULL) AND
                (first_frame_url IS NULL OR
                    (model IS NOT NULL AND model IN ('wan3.0-video', 'wan3.0-video-prime')))
            );
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_video_generation_tasks_user_model_create_time_id
    ON video_generation_tasks (
        user_id, (COALESCE(model, request_payload ->> 'model', 'happyhorse-1.1-t2v')),
        create_time DESC, id DESC
    ) WHERE is_delete = 0;

COMMENT ON TABLE video_generation_tasks IS 'HappyHorse 与万相 3.0 视频生成及七牛永久转存任务；保留幂等记录，业务删除使用逻辑删除';
COMMENT ON COLUMN video_generation_tasks.model IS '视频模型名称；历史空值从 request_payload.model 读取，不回填原请求或摘要';
COMMENT ON COLUMN video_generation_tasks.first_frame_url IS '万相 3.0 首帧 HTTP(S) 图片地址，属于受保护输入素材，可能含签名；与展示封面独立';
COMMENT ON COLUMN video_generation_tasks.last_frame_url IS '万相 3.0 尾帧 HTTP(S) 图片地址，必须同时有首帧；不记录到日志';
COMMENT ON COLUMN video_generation_tasks.qiniu_object_key IS '已确认视频对象键：HappyHorse 为 videos/happyhorse/{task_id}.mp4，万相为 videos/wan3/{task_id}.mp4';
COMMENT ON COLUMN video_generation_tasks.qiniu_cover_object_key IS '从已保存视频截帧后的独立 JPEG 对象键，与视频同前缀；未确认时为空，可重试';
