# Database migrations

Alembic is the versioned schema migration system for Vcar.

- Configure DATABASE_URL before running migrations.
- Use explicit reviewed migration scripts under migrations/versions/ until SQLAlchemy metadata models are introduced.
- Back up production data and test upgrade/downgrade on a restored copy before applying migrations in production.
- The existing app/db/schema.sql remains the legacy bootstrap schema during the compatibility period; Alembic does not automatically replay it.
