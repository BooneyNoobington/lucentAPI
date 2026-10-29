-- A database account for lucentAPI. It may read and change data, but not the schema, and it
-- can't delete: lucentBASE hides rows with vis = 0 instead. Replace the host and choose a
-- password; keep the password out of git (LUCENT_DATABASE_URL in the service's environment).

CREATE USER IF NOT EXISTS 'lucentapi'@'api.example.org' IDENTIFIED BY 'choose-a-password';
GRANT SELECT, INSERT, UPDATE ON `lucent`.* TO 'lucentapi'@'api.example.org';
