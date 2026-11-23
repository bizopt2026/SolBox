-- 베타 W 이전 모델에서 만든 전체 DDL (옛 DB 이관 시험용)
CREATE TABLE app_settings (
	"key" VARCHAR NOT NULL, 
	value JSON, 
	PRIMARY KEY ("key")
);
CREATE TABLE form_tokens (
	user_id INTEGER NOT NULL, 
	token VARCHAR NOT NULL, 
	result_url VARCHAR, 
	body_hash VARCHAR, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (user_id, token)
);
CREATE TABLE id_marks (
	table_name VARCHAR NOT NULL, 
	last_id INTEGER NOT NULL, 
	PRIMARY KEY (table_name)
);
CREATE TABLE users (
	id INTEGER NOT NULL, 
	username VARCHAR NOT NULL, 
	name VARCHAR NOT NULL, 
	password_hash VARCHAR NOT NULL, 
	role VARCHAR NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	contact VARCHAR, 
	must_change_password BOOLEAN, 
	PRIMARY KEY (id), 
	UNIQUE (username)
);
CREATE TABLE activities (
	id INTEGER NOT NULL, 
	kind VARCHAR NOT NULL, 
	date DATE, 
	title VARCHAR NOT NULL, 
	raw_text TEXT NOT NULL, 
	summary_md TEXT NOT NULL, 
	follow_ups JSON NOT NULL, 
	demand_signals JSON NOT NULL, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	created_by_id INTEGER, 
	updated_by_id INTEGER, 
	origin_type VARCHAR, 
	origin_id INTEGER, 
	PRIMARY KEY (id), 
	FOREIGN KEY(created_by_id) REFERENCES users (id), 
	FOREIGN KEY(updated_by_id) REFERENCES users (id)
);
CREATE TABLE ai_jobs (
	id INTEGER NOT NULL, 
	kind VARCHAR NOT NULL, 
	status VARCHAR NOT NULL, 
	priority INTEGER NOT NULL, 
	requester_id INTEGER, 
	input JSON NOT NULL, 
	result TEXT NOT NULL, 
	error_kind VARCHAR NOT NULL, 
	error TEXT NOT NULL, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	started_at DATETIME, 
	finished_at DATETIME, 
	PRIMARY KEY (id), 
	FOREIGN KEY(requester_id) REFERENCES users (id)
);
CREATE INDEX ix_ai_jobs_finished_at ON ai_jobs (finished_at);
CREATE TABLE bms (
	id INTEGER NOT NULL, 
	name VARCHAR NOT NULL, 
	summary VARCHAR NOT NULL, 
	target_customers TEXT NOT NULL, 
	revenue_model TEXT NOT NULL, 
	memo TEXT NOT NULL, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	created_by_id INTEGER, 
	updated_by_id INTEGER, 
	PRIMARY KEY (id), 
	FOREIGN KEY(created_by_id) REFERENCES users (id), 
	FOREIGN KEY(updated_by_id) REFERENCES users (id)
);
CREATE TABLE customers (
	id INTEGER NOT NULL, 
	name VARCHAR NOT NULL, 
	industry VARCHAR NOT NULL, 
	contact VARCHAR NOT NULL, 
	memo VARCHAR NOT NULL, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	created_by_id INTEGER, 
	updated_by_id INTEGER, 
	origin_type VARCHAR, 
	origin_id INTEGER, 
	PRIMARY KEY (id), 
	FOREIGN KEY(created_by_id) REFERENCES users (id), 
	FOREIGN KEY(updated_by_id) REFERENCES users (id)
);
CREATE TABLE research_runs (
	id INTEGER NOT NULL, 
	slot DATETIME, 
	"trigger" VARCHAR NOT NULL, 
	status VARCHAR NOT NULL, 
	requester_id INTEGER, 
	started_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	finished_at DATETIME, 
	PRIMARY KEY (id), 
	FOREIGN KEY(requester_id) REFERENCES users (id)
);
CREATE TABLE research_topics (
	id INTEGER NOT NULL, 
	name VARCHAR NOT NULL, 
	keywords VARCHAR NOT NULL, 
	area VARCHAR NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	created_by_id INTEGER, 
	updated_by_id INTEGER, 
	PRIMARY KEY (id), 
	FOREIGN KEY(created_by_id) REFERENCES users (id), 
	FOREIGN KEY(updated_by_id) REFERENCES users (id)
);
CREATE TABLE activity_customers (
	activity_id INTEGER NOT NULL, 
	customer_id INTEGER NOT NULL, 
	PRIMARY KEY (activity_id, customer_id), 
	FOREIGN KEY(activity_id) REFERENCES activities (id) ON DELETE CASCADE, 
	FOREIGN KEY(customer_id) REFERENCES customers (id) ON DELETE CASCADE
);
CREATE TABLE businesses (
	id INTEGER NOT NULL, 
	name VARCHAR NOT NULL, 
	customer_id INTEGER NOT NULL, 
	description VARCHAR NOT NULL, 
	period VARCHAR NOT NULL, 
	status VARCHAR NOT NULL, 
	memo VARCHAR NOT NULL, 
	bm_id INTEGER, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	created_by_id INTEGER, 
	updated_by_id INTEGER, 
	PRIMARY KEY (id), 
	FOREIGN KEY(customer_id) REFERENCES customers (id), 
	FOREIGN KEY(bm_id) REFERENCES bms (id), 
	FOREIGN KEY(created_by_id) REFERENCES users (id), 
	FOREIGN KEY(updated_by_id) REFERENCES users (id)
);
CREATE TABLE customer_needs (
	id INTEGER NOT NULL, 
	activity_id INTEGER NOT NULL, 
	position INTEGER NOT NULL, 
	customer_name VARCHAR NOT NULL, 
	customer_id INTEGER, 
	content TEXT NOT NULL, 
	opportunity_id INTEGER, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(activity_id) REFERENCES activities (id) ON DELETE CASCADE, 
	FOREIGN KEY(customer_id) REFERENCES customers (id) ON DELETE SET NULL
);
CREATE INDEX ix_customer_needs_activity_id ON customer_needs (activity_id);
CREATE INDEX ix_customer_needs_customer_id ON customer_needs (customer_id);
CREATE TABLE follow_ups (
	id INTEGER NOT NULL, 
	activity_id INTEGER NOT NULL, 
	position INTEGER NOT NULL, 
	task TEXT NOT NULL, 
	owner VARCHAR NOT NULL, 
	owner_user_id INTEGER, 
	due DATE, 
	done BOOLEAN NOT NULL, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(activity_id) REFERENCES activities (id) ON DELETE CASCADE, 
	FOREIGN KEY(owner_user_id) REFERENCES users (id) ON DELETE SET NULL
);
CREATE INDEX ix_follow_ups_activity_id ON follow_ups (activity_id);
CREATE TABLE intakes (
	id INTEGER NOT NULL, 
	note VARCHAR NOT NULL, 
	status VARCHAR NOT NULL, 
	"plan" JSON, 
	result JSON NOT NULL, 
	job_id INTEGER, 
	error TEXT NOT NULL, 
	applied_at DATETIME, 
	apply_token VARCHAR, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	created_by_id INTEGER, 
	updated_by_id INTEGER, 
	PRIMARY KEY (id), 
	FOREIGN KEY(job_id) REFERENCES ai_jobs (id), 
	FOREIGN KEY(created_by_id) REFERENCES users (id), 
	FOREIGN KEY(updated_by_id) REFERENCES users (id)
);
CREATE TABLE partners (
	id INTEGER NOT NULL, 
	name VARCHAR NOT NULL, 
	contact VARCHAR NOT NULL, 
	description VARCHAR NOT NULL, 
	memo VARCHAR NOT NULL, 
	customer_id INTEGER, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	created_by_id INTEGER, 
	updated_by_id INTEGER, 
	origin_type VARCHAR, 
	origin_id INTEGER, 
	PRIMARY KEY (id), 
	FOREIGN KEY(customer_id) REFERENCES customers (id) ON DELETE SET NULL, 
	FOREIGN KEY(created_by_id) REFERENCES users (id), 
	FOREIGN KEY(updated_by_id) REFERENCES users (id)
);
CREATE UNIQUE INDEX ux_partners_customer_id ON partners (customer_id);
CREATE TABLE research_reports (
	id INTEGER NOT NULL, 
	run_id INTEGER NOT NULL, 
	kind VARCHAR NOT NULL, 
	area VARCHAR NOT NULL, 
	title VARCHAR NOT NULL, 
	trends JSON NOT NULL, 
	insights JSON NOT NULL, 
	suggestions JSON NOT NULL, 
	job_id INTEGER, 
	status VARCHAR NOT NULL, 
	error TEXT NOT NULL, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(run_id) REFERENCES research_runs (id), 
	FOREIGN KEY(job_id) REFERENCES ai_jobs (id)
);
CREATE TABLE activity_businesses (
	activity_id INTEGER NOT NULL, 
	business_id INTEGER NOT NULL, 
	PRIMARY KEY (activity_id, business_id), 
	FOREIGN KEY(activity_id) REFERENCES activities (id) ON DELETE CASCADE, 
	FOREIGN KEY(business_id) REFERENCES businesses (id) ON DELETE CASCADE
);
CREATE TABLE activity_partners (
	activity_id INTEGER NOT NULL, 
	partner_id INTEGER NOT NULL, 
	PRIMARY KEY (activity_id, partner_id), 
	FOREIGN KEY(activity_id) REFERENCES activities (id) ON DELETE CASCADE, 
	FOREIGN KEY(partner_id) REFERENCES partners (id) ON DELETE CASCADE
);
CREATE TABLE intake_files (
	id INTEGER NOT NULL, 
	intake_id INTEGER NOT NULL, 
	original_name VARCHAR NOT NULL, 
	stored_name VARCHAR NOT NULL, 
	kind VARCHAR NOT NULL, 
	read_status VARCHAR NOT NULL, 
	read_error VARCHAR, 
	progress INTEGER, 
	size INTEGER NOT NULL, 
	sha256 VARCHAR, 
	target_type VARCHAR NOT NULL, 
	target_id INTEGER, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(intake_id) REFERENCES intakes (id)
);
CREATE TABLE opportunities (
	id INTEGER NOT NULL, 
	name VARCHAR NOT NULL, 
	customer_id INTEGER NOT NULL, 
	stage VARCHAR NOT NULL, 
	bm_id INTEGER, 
	project_id INTEGER, 
	memo TEXT NOT NULL, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	created_by_id INTEGER, 
	updated_by_id INTEGER, 
	PRIMARY KEY (id), 
	FOREIGN KEY(customer_id) REFERENCES customers (id), 
	FOREIGN KEY(bm_id) REFERENCES bms (id) ON DELETE SET NULL, 
	FOREIGN KEY(project_id) REFERENCES businesses (id) ON DELETE SET NULL, 
	FOREIGN KEY(created_by_id) REFERENCES users (id), 
	FOREIGN KEY(updated_by_id) REFERENCES users (id)
);
CREATE UNIQUE INDEX ux_opportunities_project_id ON opportunities (project_id);
CREATE TABLE solutions (
	id INTEGER NOT NULL, 
	name VARCHAR NOT NULL, 
	summary VARCHAR NOT NULL, 
	description VARCHAR NOT NULL, 
	partner_id INTEGER, 
	stage VARCHAR NOT NULL, 
	usage_direction VARCHAR NOT NULL, 
	applicable_types VARCHAR NOT NULL, 
	tags VARCHAR NOT NULL, 
	origin_item VARCHAR, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	created_by_id INTEGER, 
	updated_by_id INTEGER, 
	origin_type VARCHAR, 
	origin_id INTEGER, 
	PRIMARY KEY (id), 
	FOREIGN KEY(partner_id) REFERENCES partners (id), 
	FOREIGN KEY(created_by_id) REFERENCES users (id), 
	FOREIGN KEY(updated_by_id) REFERENCES users (id)
);
CREATE TABLE activity_opportunities (
	activity_id INTEGER NOT NULL, 
	opportunity_id INTEGER NOT NULL, 
	PRIMARY KEY (activity_id, opportunity_id), 
	FOREIGN KEY(activity_id) REFERENCES activities (id) ON DELETE CASCADE, 
	FOREIGN KEY(opportunity_id) REFERENCES opportunities (id) ON DELETE CASCADE
);
CREATE TABLE activity_solutions (
	activity_id INTEGER NOT NULL, 
	solution_id INTEGER NOT NULL, 
	PRIMARY KEY (activity_id, solution_id), 
	FOREIGN KEY(activity_id) REFERENCES activities (id) ON DELETE CASCADE, 
	FOREIGN KEY(solution_id) REFERENCES solutions (id) ON DELETE CASCADE
);
CREATE TABLE bm_solutions (
	bm_id INTEGER NOT NULL, 
	solution_id INTEGER NOT NULL, 
	PRIMARY KEY (bm_id, solution_id), 
	FOREIGN KEY(bm_id) REFERENCES bms (id) ON DELETE CASCADE, 
	FOREIGN KEY(solution_id) REFERENCES solutions (id) ON DELETE CASCADE
);
CREATE TABLE opportunity_solutions (
	opportunity_id INTEGER NOT NULL, 
	solution_id INTEGER NOT NULL, 
	PRIMARY KEY (opportunity_id, solution_id), 
	FOREIGN KEY(opportunity_id) REFERENCES opportunities (id) ON DELETE CASCADE, 
	FOREIGN KEY(solution_id) REFERENCES solutions (id) ON DELETE CASCADE
);
CREATE TABLE proposals (
	id INTEGER NOT NULL, 
	title VARCHAR NOT NULL, 
	customer_id INTEGER NOT NULL, 
	business_id INTEGER, 
	opportunity_id INTEGER, 
	request_memo TEXT NOT NULL, 
	status VARCHAR NOT NULL, 
	extra_solution_ids JSON NOT NULL, 
	directions JSON NOT NULL, 
	selected_index INTEGER, 
	outline JSON, 
	outline_updated_at DATETIME, 
	deck_name VARCHAR NOT NULL, 
	deck_built_at DATETIME, 
	deck_removed INTEGER, 
	active_job_id INTEGER, 
	chat_from_id INTEGER, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	created_by_id INTEGER, 
	updated_by_id INTEGER, 
	PRIMARY KEY (id), 
	FOREIGN KEY(customer_id) REFERENCES customers (id), 
	FOREIGN KEY(business_id) REFERENCES businesses (id), 
	FOREIGN KEY(opportunity_id) REFERENCES opportunities (id) ON DELETE SET NULL, 
	FOREIGN KEY(active_job_id) REFERENCES ai_jobs (id), 
	FOREIGN KEY(created_by_id) REFERENCES users (id), 
	FOREIGN KEY(updated_by_id) REFERENCES users (id)
);
CREATE TABLE solution_files (
	id INTEGER NOT NULL, 
	solution_id INTEGER NOT NULL, 
	original_name VARCHAR NOT NULL, 
	stored_name VARCHAR NOT NULL, 
	kind VARCHAR NOT NULL, 
	extract_status VARCHAR NOT NULL, 
	size INTEGER NOT NULL, 
	sha256 VARCHAR, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	created_by_id INTEGER, 
	updated_by_id INTEGER, 
	PRIMARY KEY (id), 
	FOREIGN KEY(solution_id) REFERENCES solutions (id), 
	FOREIGN KEY(created_by_id) REFERENCES users (id), 
	FOREIGN KEY(updated_by_id) REFERENCES users (id)
);
CREATE TABLE proposal_messages (
	id INTEGER NOT NULL, 
	proposal_id INTEGER NOT NULL, 
	role VARCHAR NOT NULL, 
	content TEXT NOT NULL, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(proposal_id) REFERENCES proposals (id)
);
CREATE TABLE proposal_solutions (
	proposal_id INTEGER NOT NULL, 
	solution_id INTEGER NOT NULL, 
	PRIMARY KEY (proposal_id, solution_id), 
	FOREIGN KEY(proposal_id) REFERENCES proposals (id) ON DELETE CASCADE, 
	FOREIGN KEY(solution_id) REFERENCES solutions (id) ON DELETE CASCADE
);
