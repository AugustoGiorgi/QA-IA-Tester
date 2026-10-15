import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import HTTPException
from services.sql_assistant import (
    COLUMN_HEADERS,
    RELATION_HEADERS,
    _csv_records,
    _explicit_business_definition,
    _schema_table,
    _select_schema_context,
    _validate_select,
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

    def test_definition_detection_avoids_normal_query_values(self):
        self.assertTrue(_explicit_business_definition("Una póliza vigente significa estado A y fecha fin posterior a hoy"))
        self.assertTrue(_explicit_business_definition("Para considerar vigente, debe tener estado activo"))
        self.assertFalse(_explicit_business_definition("Dame las pólizas vigentes del ramo 25"))


if __name__ == "__main__":
    unittest.main()
