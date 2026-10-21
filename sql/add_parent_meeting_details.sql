-- Run once against each school's SQLite database.
-- These columns are nullable so existing visit records remain valid.
ALTER TABLE visits ADD COLUMN parent_name VARCHAR(120);
ALTER TABLE visits ADD COLUMN parent_relationship VARCHAR(20);
ALTER TABLE visits ADD COLUMN parent_phone VARCHAR(30);
