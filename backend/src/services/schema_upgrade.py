from sqlalchemy import inspect, text


def upgrade(engine):
    """Bring demo volumes created before retention plans up to the current schema.

    ``MetaData.create_all`` only adds missing tables, so an existing evidence table
    needs the nullable ``plan_id`` column and relaxed ``run_id`` by hand. Every step
    is idempotent; signed report/digest columns are never touched.
    """
    inspector = inspect(engine)
    if 'business_evidence' not in inspector.get_table_names():
        return
    with engine.begin() as conn:
        columns = {column['name']: column for column in inspect(conn).get_columns('business_evidence')}
        if 'plan_id' not in columns:
            conn.execute(text('ALTER TABLE business_evidence ADD COLUMN plan_id integer'))
            conn.execute(text('ALTER TABLE business_evidence ADD CONSTRAINT business_evidence_plan_id_fkey '
                              'FOREIGN KEY (plan_id) REFERENCES retention_plans (id)'))
            conn.execute(text('CREATE UNIQUE INDEX IF NOT EXISTS ix_business_evidence_plan_id '
                              'ON business_evidence (plan_id)'))
        if not columns.get('run_id', {}).get('nullable', True):
            conn.execute(text('ALTER TABLE business_evidence ALTER COLUMN run_id DROP NOT NULL'))
