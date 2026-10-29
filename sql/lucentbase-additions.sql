-- Columns lucentAPI needs that lucentBASE doesn't have yet. Safe to run more than once
-- (MariaDB 10.1.4 or later). Test it on a copy of the database first.

-- When and by whom a collection was sampled (UTC).
ALTER TABLE `collection`
    ADD COLUMN IF NOT EXISTS `sampled_at` DATETIME NULL,
    ADD COLUMN IF NOT EXISTS `id_sampler` INT NULL;

-- Identifiers come from the numbering scheme and must not repeat.
CREATE UNIQUE INDEX IF NOT EXISTS `collection_name` ON `collection` (`name`);

-- The norm a method follows, e.g. DIN EN ISO 10523.
ALTER TABLE `action`
    ADD COLUMN IF NOT EXISTS `standard` VARCHAR(255) NULL;

-- The unit of the value (uncertainty_id_unit is the unit of the uncertainty), the display
-- precision and the limits the value is checked against.
ALTER TABLE `j_action_variable`
    ADD COLUMN IF NOT EXISTS `id_unit` INT NULL,
    ADD COLUMN IF NOT EXISTS `decimals` SMALLINT NULL,
    ADD COLUMN IF NOT EXISTS `lower_limit` DOUBLE NULL,
    ADD COLUMN IF NOT EXISTS `upper_limit` DOUBLE NULL;

-- Whether a task is done.
ALTER TABLE `task`
    ADD COLUMN IF NOT EXISTS `done` BOOL NOT NULL DEFAULT 0;

-- When a message was sent (UTC).
ALTER TABLE `message`
    ADD COLUMN IF NOT EXISTS `sent_at` DATETIME NULL;
