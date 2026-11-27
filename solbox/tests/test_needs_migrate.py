"""고객니즈 관리 (2026-11-27): 옛 DB 의 customer_needs 표(활동 칸 필수·CASCADE)를 새 모양으로 한 번 다시 만든다."""
import sqlite3

from app import migrate
from app.db import make_engine
from app.models import Base

OLD_NEEDS = """CREATE TABLE customer_needs (
    id INTEGER NOT NULL,
    activity_id INTEGER NOT NULL,
    position INTEGER NOT NULL,
    customer_name VARCHAR NOT NULL,
    customer_id INTEGER,
    content TEXT NOT NULL,
    opportunity_id INTEGER,
    created_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL,
    updated_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(activity_id) REFERENCES activities (id) ON DELETE CASCADE,
    FOREIGN KEY(customer_id) REFERENCES customers (id) ON DELETE SET NULL
)"""


def _old_db(db_path):
    """새 모양으로 만든 뒤 customer_needs 만 옛 모양으로 바꾸고 줄을 넣는다."""
    engine = make_engine(db_path)
    Base.metadata.create_all(engine)
    engine.dispose()
    con = sqlite3.connect(db_path)
    con.execute("DROP TABLE customer_needs")
    con.execute(OLD_NEEDS)
    con.execute("CREATE INDEX ix_customer_needs_activity_id ON customer_needs (activity_id)")
    con.execute("INSERT INTO customers (id, name, industry, contact, memo, aliases) VALUES (1, '가나제지', '', '', '', '')")
    con.execute("INSERT INTO activities (id, kind, title, raw_text, summary_md, follow_ups, demand_signals) VALUES (1, 'meeting', '통화', 'r', '', '[]', '[]')")
    con.executemany(
        "INSERT INTO customer_needs (id, activity_id, position, customer_name, customer_id, content, opportunity_id) VALUES (?, 1, ?, '가나제지', 1, ?, ?)",
        [(3, 0, "전기요금 부담", None), (7, 1, "초기 투자 어려움", 5), (9, 2, "옥상 공간 있음", None)])
    con.commit()
    con.close()


def _rows(db_path):
    con = sqlite3.connect(db_path)
    out = con.execute("SELECT id, activity_id, content, opportunity_id, status, memo, field_id FROM customer_needs ORDER BY id").fetchall()
    con.close()
    return out


def _notnull(db_path, col):
    con = sqlite3.connect(db_path)
    info = {r[1]: r for r in con.execute("PRAGMA table_info(customer_needs)")}
    con.close()
    return info[col][3]


def test_old_needs_table_is_rebuilt_once_keeping_rows(tmp_path):
    db_path = tmp_path / "app.db"
    _old_db(db_path)
    engine = make_engine(db_path)
    assert migrate.customer_needs_need_rebuild(engine) is True
    migrate.run_startup_migrations(engine, db_path)
    assert migrate.customer_needs_need_rebuild(engine) is False
    assert migrate.rebuild_customer_needs(engine, db_path) is None  # 한 번만
    engine.dispose()
    assert _rows(db_path) == [(3, 1, "전기요금 부담", None, "new", "", None), (7, 1, "초기 투자 어려움", 5, "new", "", None),
                              (9, 1, "옥상 공간 있음", None, "new", "", None)]
    assert _notnull(db_path, "activity_id") == 0
    assert len(list(tmp_path.glob("app.db.before-migrate-*"))) == 1


def test_deleting_activity_after_rebuild_keeps_needs(tmp_path):
    db_path = tmp_path / "app.db"
    _old_db(db_path)
    engine = make_engine(db_path)
    migrate.run_startup_migrations(engine, db_path)
    engine.dispose()
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("DELETE FROM activities WHERE id = 1")
    con.commit()
    left = con.execute("SELECT id, activity_id FROM customer_needs ORDER BY id").fetchall()
    con.close()
    assert left == [(3, None), (7, None), (9, None)]  # DB 규칙은 비우기만 한다 (손대지 않은 줄 지우기는 응용 코드)


def test_fresh_database_needs_no_needs_rebuild(app, settings):
    assert migrate.customer_needs_need_rebuild(app.state.engine) is False
    assert migrate.rebuild_customer_needs(app.state.engine, settings.db_path) is None
