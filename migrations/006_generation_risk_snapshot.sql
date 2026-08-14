-- Run only after 005_generation_previews_and_deletion.sql in the explicitly
-- approved database, with the backend stopped and a verified history backup.
-- Existing rows remain unchanged with risk_assessment NULL.  NULL means that
-- no generation-time snapshot exists; the application must not rescan those
-- rows with newer rules and present that result as historical evidence.

ALTER TABLE generation_records
    ADD COLUMN risk_assessment JSON NULL AFTER tags;
