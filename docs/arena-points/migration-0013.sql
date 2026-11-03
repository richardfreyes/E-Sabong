BEGIN;

-- Running upgrade 0012 -> 0013

CREATE FUNCTION points_retired() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'The points module is retired: % is a read-only archive', TG_TABLE_NAME
            USING ERRCODE = 'insufficient_privilege';
        END $$;;

CREATE TRIGGER point_accounts_retired BEFORE INSERT OR UPDATE OR DELETE ON point_accounts FOR EACH ROW EXECUTE FUNCTION points_retired();

CREATE TRIGGER point_transactions_retired BEFORE INSERT OR UPDATE OR DELETE ON point_transactions FOR EACH ROW EXECUTE FUNCTION points_retired();

CREATE TRIGGER point_ledger_entries_retired BEFORE INSERT OR UPDATE OR DELETE ON point_ledger_entries FOR EACH ROW EXECUTE FUNCTION points_retired();

UPDATE alembic_version SET version_num='0013' WHERE alembic_version.version_num = '0012';

COMMIT;

