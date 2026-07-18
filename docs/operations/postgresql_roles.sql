-- PostgreSQL Role（数据库角色）模板。变量由受控 psql 会话提供；不要写入密码。
-- Required variables: migration_role, app_role, maintenance_role, database_name.

REVOKE CREATE ON SCHEMA public FROM PUBLIC;

GRANT CONNECT ON DATABASE :"database_name" TO :"app_role", :"maintenance_role";
GRANT USAGE ON SCHEMA public TO :"app_role", :"maintenance_role";

GRANT SELECT ON alembic_version TO :"app_role";
GRANT SELECT, INSERT, UPDATE ON agent_requests TO :"app_role";
GRANT SELECT, INSERT ON agent_runs TO :"app_role";
GRANT SELECT, INSERT ON audit_events TO :"app_role";
GRANT SELECT, INSERT, UPDATE ON idempotency_records TO :"app_role";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO :"app_role";

GRANT SELECT, DELETE ON idempotency_records TO :"maintenance_role";

ALTER DEFAULT PRIVILEGES FOR ROLE :"migration_role" IN SCHEMA public
    GRANT SELECT, INSERT ON TABLES TO :"app_role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"migration_role" IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO :"app_role";

-- Restore（恢复）后必须重新审核 Owner（所有者）、Grant（授权）和 Default Privilege（默认权限）。
