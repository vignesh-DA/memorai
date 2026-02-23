-- Add missing last_used_turn column to memories table

ALTER TABLE memories 
ADD COLUMN IF NOT EXISTS last_used_turn INTEGER DEFAULT NULL;

-- Add index for performance
CREATE INDEX IF NOT EXISTS idx_memories_last_used_turn 
ON memories(last_used_turn);

-- Add missing importance_score and importance_level columns if needed
ALTER TABLE memories 
ADD COLUMN IF NOT EXISTS importance_score FLOAT DEFAULT 0.7;

ALTER TABLE memories 
ADD COLUMN IF NOT EXISTS importance_level VARCHAR(20) DEFAULT 'medium';

SELECT 'Schema fixed successfully!' AS status;
