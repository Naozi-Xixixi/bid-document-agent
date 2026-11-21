-- Run this once as a MySQL administrator.
-- Replace every CHANGE_ME_STRONG_PASSWORD value before execution.
-- The global CREATE privilege lets the service provision a database when a
-- new company is added; all other privileges are limited to wordagent_* DBs.
CREATE USER IF NOT EXISTS 'wordagent'@'127.0.0.1' IDENTIFIED BY 'hjw123456';
CREATE USER IF NOT EXISTS 'wordagent'@'localhost' IDENTIFIED BY 'hjw123456';
ALTER USER 'wordagent'@'127.0.0.1' IDENTIFIED BY 'hjw123456';
ALTER USER 'wordagent'@'localhost' IDENTIFIED BY 'hjw123456';
-- CREATE DATABASE is required for onboarding new company databases.
GRANT CREATE ON *.* TO 'wordagent'@'127.0.0.1';
GRANT CREATE ON *.* TO 'wordagent'@'localhost';
GRANT CREATE, ALTER, INDEX, INSERT, UPDATE, DELETE, SELECT, REFERENCES, CREATE VIEW, SHOW VIEW
  ON `wordagent\_%`.* TO 'wordagent'@'127.0.0.1';
GRANT CREATE, ALTER, INDEX, INSERT, UPDATE, DELETE, SELECT, REFERENCES, CREATE VIEW, SHOW VIEW
  ON `wordagent\_%`.* TO 'wordagent'@'localhost';
FLUSH PRIVILEGES;
