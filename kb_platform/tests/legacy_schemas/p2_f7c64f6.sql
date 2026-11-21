-- Exact SQLite schema produced by Base.metadata.create_all() of kb_platform/models.py at commit f7c64f6
-- (frozen so the migration test does not depend on git history)
CREATE TABLE roles (
	id INTEGER NOT NULL, 
	name VARCHAR(64) NOT NULL, 
	description VARCHAR(255) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (name)
);
CREATE TABLE departments (
	id INTEGER NOT NULL, 
	name VARCHAR(64) NOT NULL, 
	description VARCHAR(255) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (name)
);
CREATE TABLE users (
	id INTEGER NOT NULL, 
	username VARCHAR(64) NOT NULL, 
	display_name VARCHAR(128) NOT NULL, 
	password_hash VARCHAR(255) NOT NULL, 
	clearance INTEGER NOT NULL, 
	active BOOLEAN NOT NULL, 
	is_admin BOOLEAN NOT NULL, 
	failed_logins INTEGER NOT NULL, 
	locked_until DATETIME, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (username)
);
CREATE TABLE knowledge_bases (
	id VARCHAR(32) NOT NULL, 
	name VARCHAR(128) NOT NULL, 
	description VARCHAR(1000) NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (name)
);
CREATE TABLE acl_state (
	id INTEGER NOT NULL, 
	version INTEGER NOT NULL, 
	PRIMARY KEY (id)
);
CREATE TABLE audit_log (
	id INTEGER NOT NULL, 
	ts DATETIME NOT NULL, 
	request_id VARCHAR(32) NOT NULL, 
	actor VARCHAR(64) NOT NULL, 
	action VARCHAR(64) NOT NULL, 
	resource VARCHAR(128) NOT NULL, 
	outcome VARCHAR(32) NOT NULL, 
	detail JSON NOT NULL, 
	PRIMARY KEY (id)
);
CREATE TABLE user_roles (
	user_id INTEGER NOT NULL, 
	role_id INTEGER NOT NULL, 
	PRIMARY KEY (user_id, role_id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
	FOREIGN KEY(role_id) REFERENCES roles (id) ON DELETE CASCADE
);
CREATE TABLE user_departments (
	user_id INTEGER NOT NULL, 
	department_id INTEGER NOT NULL, 
	PRIMARY KEY (user_id, department_id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
	FOREIGN KEY(department_id) REFERENCES departments (id) ON DELETE CASCADE
);
CREATE TABLE auth_tokens (
	id INTEGER NOT NULL, 
	token_sha256 VARCHAR(64) NOT NULL, 
	user_id INTEGER NOT NULL, 
	created_at DATETIME NOT NULL, 
	expires_at DATETIME NOT NULL, 
	revoked BOOLEAN NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (token_sha256), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);
CREATE TABLE documents (
	id VARCHAR(32) NOT NULL, 
	kb_id VARCHAR(32) NOT NULL, 
	doc_key VARCHAR(255) NOT NULL, 
	title VARCHAR(255) NOT NULL, 
	classification VARCHAR(32) NOT NULL, 
	owner_department VARCHAR(64), 
	status VARCHAR(16) NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_documents_doc_key UNIQUE (doc_key), 
	FOREIGN KEY(kb_id) REFERENCES knowledge_bases (id) ON DELETE CASCADE
);
CREATE TABLE chat_sessions (
	id VARCHAR(32) NOT NULL, 
	user_id INTEGER NOT NULL, 
	kb_id VARCHAR(32) NOT NULL, 
	title VARCHAR(255) NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
	FOREIGN KEY(kb_id) REFERENCES knowledge_bases (id) ON DELETE CASCADE
);
CREATE TABLE document_grants (
	id INTEGER NOT NULL, 
	document_id VARCHAR(32) NOT NULL, 
	effect VARCHAR(8) NOT NULL, 
	subject_type VARCHAR(32) NOT NULL, 
	subject_value VARCHAR(64) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_grant UNIQUE (document_id, effect, subject_type, subject_value), 
	FOREIGN KEY(document_id) REFERENCES documents (id) ON DELETE CASCADE
);
CREATE TABLE chat_messages (
	id INTEGER NOT NULL, 
	session_id VARCHAR(32) NOT NULL, 
	question TEXT NOT NULL, 
	answer TEXT NOT NULL, 
	citations JSON NOT NULL, 
	status VARCHAR(48) NOT NULL, 
	scope_key VARCHAR(64) NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(session_id) REFERENCES chat_sessions (id) ON DELETE CASCADE
);
