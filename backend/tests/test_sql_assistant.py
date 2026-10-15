import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import HTTPException
from services.sql_assistant import (
    COLUMN_HEADERS,
    RELATION_HEADERS,
    _csv_records,
    _schema_table,
    _select_schema_context,
    _validate_select,
    router,
    MessageIn,
    send_message,
    KNOWLEDGE,
    SCHEMA_COLUMNS,
    SCHEMA_META,
    SCHEMA_RELATIONS,
    THREADS,
)


class SqlAssistantTests(unittest.TestCase):
    def test_imports_column_and_relation_csv_headers(self):
        columns = (
            '"ESQUEMA","TABLA","POSICION","COLUMNA","TIPO","LARGO","PRECISION","ESCALA","ADMITE_NULOS"\n'
            '"INSUDB","POLICY",1,"NPOLICY","NUMBER",22,10,0,"N"\n'
            '"INSUDB","POLICY",2,"DEXPIRDAT","DATE",7,,,"Y"\n'
        ).encode()
        relations = (
            '"ESQUEMA","TABLA","CLAVE_FORANEA","POSICION","COLUMNA","ESQUEMA_REFERENCIADO","TABLA_REFERENCIADA","CLAVE_REFERENCIADA","COLUMNA_REFERENCIADA"\n'
            '"INSUDB","POLICY","FK_POLICY_CLIENT",1,"NCLIENT","INSUDB","CLIENT","PK_CLIENT","NCLIENT"\n'
        ).encode()
        column_rows = _csv_records(columns, COLUMN_HEADERS, "columnas")
        relation_rows = _csv_records(relations, RELATION_HEADERS, "relaciones")
        schema = _schema_table(column_rows)
        self.assertEqual(len(schema["tables"]), 1)
        self.assertEqual(schema["column_count"], 2)
        self.assertEqual(relation_rows[0]["TABLA_REFERENCIADA"], "CLIENT")

    def test_rejects_wrong_schema_file(self):
        with self.assertRaises(HTTPException) as raised:
            _csv_records(b"OWNER,TABLE\nA,B\n", COLUMN_HEADERS, "columnas")
        self.assertEqual(raised.exception.status_code, 400)

    def test_retrieves_tables_from_spanish_query_and_relationships(self):
        columns = [
            {"owner": "INSUDB", "table": "POLICY", "column": "NPOLICY", "type": "NUMBER"},
            {"owner": "INSUDB", "table": "POLICY", "column": "DEXPIRDAT", "type": "DATE"},
            {"owner": "INSUDB", "table": "CLIENT", "column": "NCLIENT", "type": "NUMBER"},
            {"owner": "INSUDB", "table": "CLAIM", "column": "NCLAIM", "type": "NUMBER"},
        ]
        relations = [{"owner": "INSUDB", "table": "POLICY", "column": "NCLIENT", "ref_owner": "INSUDB", "ref_table": "CLIENT", "ref_column": "NCLIENT"}]
        result = _select_schema_context(columns, relations, "buscar pólizas vigentes del cliente")
        self.assertIn('"table":"POLICY"', result)
        self.assertIn('"table":"CLIENT"', result)
        self.assertNotIn('"table":"CLAIM"', result)

    def test_plural_business_words_match_schema_hints(self):
        columns = [{"owner": "INSUDB", "table": "POLICY", "column": "NPOLICY", "type": "NUMBER"}]
        result = _select_schema_context(columns, [], "pólizas vigentes")
        self.assertIn('"table":"POLICY"', result)

    def test_only_accepts_one_read_only_select(self):
        self.assertEqual(_validate_select("SELECT p.NPOLICY FROM INSUDB.POLICY p;"), "SELECT p.NPOLICY FROM INSUDB.POLICY p")
        self.assertEqual(_validate_select("SELECT COUNT(*) FROM INSUDB.POLICY"), "SELECT COUNT(*) FROM INSUDB.POLICY")
        for sql in (
            "SELECT * FROM POLICY",
            "SELECT p.* FROM POLICY p",
            "UPDATE POLICY SET SSTATUS_POL = 'A'",
            "SELECT * FROM POLICY; DELETE FROM POLICY",
            "WITH x AS (SELECT 1 FROM dual) SELECT * FROM x",
            "SELECT NPOLICY FROM POLICY /* UPDATE */ WHERE NPOLICY=1; DROP TABLE POLICY",
        ):
            with self.subTest(sql=sql), self.assertRaises(ValueError):
                _validate_select(sql)

    def test_catalog_cannot_be_changed_through_public_upload_route(self):
        routes = {(route.path, tuple(route.methods or [])) for route in router.routes}
        self.assertFalse(any(path.endswith("/schema") and "POST" in methods for path, methods in routes))

    def test_one_persistent_shared_conversation_is_available(self):
        paths = {route.path for route in router.routes}
        self.assertIn("/api/sql-assistant/conversation", paths)
        self.assertIn("/api/sql-assistant/conversation/messages", paths)


class AsyncCursor:
    def __init__(self, rows):
        self.rows = rows

    def limit(self, _count):
        return self

    async def to_list(self, length=None):
        return self.rows[:length]


class FakeCollection:
    def __init__(self, one=None, rows=None):
        self.one = one
        self.rows = rows or []
        self.updates = []

    async def find_one(self, _query):
        return self.one

    def find(self, _query, _projection=None):
        return AsyncCursor(self.rows)

    async def update_one(self, query, update, upsert=False):
        self.updates.append((query, update, upsert))


class FakeDatabase:
    def __init__(self, collections):
        self.collections = collections

    def __getitem__(self, name):
        return self.collections[name]


class SqlAssistantAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_normal_chat_does_not_save_definition_to_knowledge(self):
        collections = {
            THREADS: FakeCollection(one={"_id": "shared", "title": "Chat SQL del equipo", "messages": []}),
            SCHEMA_META: FakeCollection(one={"_id": "current", "catalog_version": "v1"}),
            SCHEMA_COLUMNS: FakeCollection(rows=[{"owner": "INSUDB", "table": "POLICY", "column": "NPOLICY", "type": "NUMBER"}]),
            SCHEMA_RELATIONS: FakeCollection(),
            KNOWLEDGE: FakeCollection(),
        }
        response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"status":"question","reply":"¿Qué significa vigente?","sql":"","assumptions":[],"knowledge":{"term":"póliza vigente","definition":"Estado activo"}}'))])
        with patch("services.sql_assistant._db", return_value=FakeDatabase(collections)), patch("services.sql_assistant.chat_completion_async", new=AsyncMock(return_value=response)):
            await send_message(MessageIn(message="Vigente significa estado activo"), {"username": "shared"})
        self.assertEqual(collections[KNOWLEDGE].updates, [])
        self.assertTrue(any("messages" in update.get("$set", {}) for _, update, _ in collections[THREADS].updates))


if __name__ == "__main__":
    unittest.main()
