CREATE TABLE IF NOT EXISTS account_profiles(user_id TEXT PRIMARY KEY REFERENCES users(id),verified INTEGER NOT NULL DEFAULT 0,plan TEXT NOT NULL DEFAULT 'trial');
CREATE TABLE IF NOT EXISTS mail_tokens(token TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),kind TEXT NOT NULL,expires INTEGER NOT NULL,used INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS archives(id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),semester TEXT NOT NULL,course_name TEXT NOT NULL,device_id TEXT,course_id INTEGER,automatic INTEGER NOT NULL DEFAULT 0,language TEXT NOT NULL DEFAULT 'zh',created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS archive_files(id TEXT PRIMARY KEY,archive_id TEXT NOT NULL REFERENCES archives(id),source_key TEXT NOT NULL,name TEXT NOT NULL,group_name TEXT NOT NULL,object_key TEXT NOT NULL,bytes INTEGER NOT NULL,sha TEXT NOT NULL,text_json TEXT NOT NULL DEFAULT '[]',updated INTEGER NOT NULL,UNIQUE(archive_id,source_key));
CREATE TABLE IF NOT EXISTS uploads(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,archive_id TEXT NOT NULL,source_key TEXT NOT NULL,name TEXT NOT NULL,group_name TEXT NOT NULL,bytes INTEGER NOT NULL,sha TEXT NOT NULL,expires INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'pending');
CREATE TABLE IF NOT EXISTS shares(id TEXT PRIMARY KEY,archive_id TEXT NOT NULL REFERENCES archives(id),email TEXT,token TEXT UNIQUE,revoked INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS summaries(id TEXT PRIMARY KEY,archive_id TEXT NOT NULL REFERENCES archives(id),user_id TEXT NOT NULL,fingerprint TEXT NOT NULL,language TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'queued',result TEXT NOT NULL DEFAULT '',progress TEXT NOT NULL DEFAULT '{}',updated INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS usage_counters(key TEXT PRIMARY KEY,amount INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS archive_owner ON archives(user_id);
CREATE INDEX IF NOT EXISTS summary_queue ON summaries(status,updated);

CREATE TABLE IF NOT EXISTS archive_selection(archive_id TEXT PRIMARY KEY,ids TEXT NOT NULL DEFAULT '[]');

CREATE UNIQUE INDEX IF NOT EXISTS one_pending_upload ON uploads(archive_id,source_key) WHERE status='pending';

CREATE TABLE IF NOT EXISTS archive_exclusions(archive_id TEXT NOT NULL,source_key TEXT NOT NULL,PRIMARY KEY(archive_id,source_key));
