"""Project deletion tests only use F-drive disposable tenant databases/files."""
from __future__ import annotations

from contextlib import ExitStack, closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from app.core.config import settings
from app.core.tenant_context import TenantContext, set_current_tenant, reset_current_tenant
from app.schemas.tender import UploadedFileInfo
from app.services import storage, project_deletion_service as service
from app.services.project_activity import project_request


class ProjectDeletionTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        parent = Path(__file__).resolve().parents[2] / ".cache" / "deletion-tests"
        parent.mkdir(parents=True, exist_ok=True)
        self.temp = self.stack.enter_context(tempfile.TemporaryDirectory(dir=parent))
        self.root = Path(self.temp)
        self.stack.enter_context(patch.object(settings, "db_backend", "sqlite"))
        self.stack.enter_context(patch.object(settings, "data_dir", self.root))
        self.tenants = {name: TenantContext(1, "offline", "admin", name, name, self.root / name / "db.sqlite", self.root / "uploads" / "companies" / name) for name in ("a", "b")}
        token = set_current_tenant(self.tenants["a"])
        self.stack.callback(reset_current_tenant, token)
        self.connections = []
        original_connect = storage.connect
        def connect():
            connection = original_connect()
            self.connections.append(connection)
            return connection
        self.stack.enter_context(patch.object(storage, "connect", side_effect=connect))
        self.stack.callback(self.close_connections)
        self.project = storage.create_project()
        self.bid_id = storage.get_bid_id(self.project.id)
        self.other = storage.create_project()
        self.other_bid = storage.get_bid_id(self.other.id)

    def close_connections(self):
        for conn in self.connections:
            conn.close()

    def sql(self, sql, args=()):
        with closing(storage.connect()) as conn:
            cursor = conn.execute(sql, args)
            rows = cursor.fetchall()
            conn.commit()
            return rows

    def file(self, relative, body=b"owned copy"):
        path = settings.upload_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        return path

    def insert(self, table, **values):
        with closing(storage.connect()) as conn:
            for col in conn.execute(f"PRAGMA table_info({table})"):
                if col["notnull"] and col["dflt_value"] is None and not col["pk"] and col["name"] not in values:
                    values[col["name"]] = 1 if "INT" in col["type"] else "offline"
            keys = list(values)
            cursor = conn.execute(f"INSERT INTO {table} ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})", tuple(values[key] for key in keys))
            conn.commit()
            return cursor.lastrowid

    def seed_relations(self, bid):
        version = self.insert("bid_catalog_versions", bid_id=bid)
        chapter = self.insert("bid_catalog_chapters", bid_id=bid, version_id=version, level=1)
        self.insert("bid_parse_info", bid_id=bid, parse_status="completed")
        self.insert("bid_list_files", bid_id=bid, list_type="upload")
        self.insert("bid_reference_materials", bid_id=bid, material_type="template", parse_status="completed")
        self.insert("bid_mermaid_assets", bid_id=bid)
        self.insert("bid_chapter_contents", bid_id=bid, version_id=version, chapter_id=chapter, status="completed")
        self.insert("bid_chapter_material_selections", bid_id=bid, chapter_id=chapter)
        task_id = f"task-{bid}"
        self.insert("bid_analysis_tasks", task_id=task_id, bid_id=bid, status="completed")
        self.insert("bid_analysis_result", task_id=task_id, bid_id=bid, chapter_id=chapter, risk_level="suggestion")
        self.insert("bid_analysis_documents", bid_id=bid)
        item_id = self.insert("knowledge_items")
        self.insert("knowledge_source_refs", item_id=item_id, bid_id=bid, chapter_id=chapter)
        self.sql("CREATE TABLE IF NOT EXISTS bid_audit_result (audit_id INTEGER PRIMARY KEY, bid_id INTEGER, tender_file_path TEXT, bid_file_path TEXT, status TEXT)")
        self.insert("bid_audit_result", bid_id=bid, status="completed")
        return item_id

    def test_deletes_all_owned_relations_files_but_keeps_other_project_and_library(self):
        library = self.seed_relations(self.bid_id)
        self.seed_relations(self.other_bid)
        own = self.file(f"bids/{self.bid_id}/tender/a.docx")
        analysis = self.file(f"analysis/{self.bid_id}/a.docx")
        other = self.file(f"bids/{self.other_bid}/tender/b.docx")
        legacy = self.file("legacy.docx")
        self.project.files.append(UploadedFileInfo(fileId=legacy.name, filename="legacy.docx", size=10, contentType="application/test", projectPath="uploads/legacy.docx"))
        storage.save_project(self.project)
        self.sql("CREATE TABLE p1_projects (project_id TEXT)")
        self.sql("INSERT INTO p1_projects VALUES (?)", (self.project.id,))
        self.sql("CREATE TABLE bid_project (project_id INTEGER)")
        self.sql("INSERT INTO bid_project VALUES (?)", (self.bid_id,))
        result = service.delete_project(self.project.id)
        self.assertEqual(result["bidId"], self.bid_id)
        self.assertEqual(result["cleanup"], {"status": "completed", "removed": 3, "preservedShared": 0})
        for table in service._BID_TABLES:
            self.assertFalse(self.sql(f"SELECT 1 FROM {table} WHERE bid_id = ?", (self.bid_id,)), table)
        self.assertFalse(own.exists() or analysis.exists() or legacy.exists())
        self.assertTrue(other.is_file())
        self.assertEqual(storage.load_project(self.other.id).id, self.other.id)
        self.assertTrue(self.sql("SELECT 1 FROM knowledge_items WHERE item_id = ?", (library,)))
        self.assertTrue(self.sql("SELECT 1 FROM p1_projects"))
        self.assertTrue(self.sql("SELECT 1 FROM bid_project"))
        with self.assertRaises(FileNotFoundError):
            storage.save_project(self.project)
        self.assertFalse(self.sql("SELECT 1 FROM projects WHERE id = ?", (self.project.id,)))

    def test_preserves_knowledge_assets_and_other_project_markdown_and_parse_refs(self):
        asset = self.file(f"bids/{self.bid_id}/graphic_images/shared.png")
        markdown = self.file(f"bids/{self.bid_id}/graphic_images/markdown.png")
        tender = self.file(f"bids/{self.bid_id}/tender/shared.docx")
        private = self.file(f"bids/{self.bid_id}/tender/private.docx")
        self.sql("CREATE TABLE knowledge_asset (asset_id INTEGER PRIMARY KEY, resource_path TEXT)")
        self.sql("INSERT INTO knowledge_asset(resource_path) VALUES (?)", (str(asset),))
        # Nondefault company data-dir-relative format used by merge-parse.
        self.insert("bid_parse_info", bid_id=self.other_bid, tender_file_path=tender.relative_to(self.root).as_posix(), parse_status="completed")
        # Store a direct link in another project's valid serialized state.
        self.sql("UPDATE projects SET state_json = ? WHERE id = ?", (self.other.model_dump_json().replace('"files":[]', '"files":[]'), self.other.id))
        version = self.insert("bid_catalog_versions", bid_id=self.other_bid)
        self.insert("bid_catalog_chapters", bid_id=self.other_bid, version_id=version, level=1, graphic_markdown=f"![图]({markdown.as_posix()})")
        result = service.delete_project(self.project.id)
        self.assertEqual(result["cleanup"]["preservedShared"], 3)
        self.assertTrue(asset.exists() and markdown.exists() and tender.exists())
        self.assertFalse(private.exists())
        # Once the last project reference disappears, old owned copies release.
        second = service.delete_project(self.other.id)
        self.assertEqual(second["cleanup"]["removed"], 2)
        self.assertFalse(markdown.exists() or tender.exists())
        self.assertTrue(asset.exists())

    def test_other_tenant_cannot_delete_and_same_id_is_independent(self):
        original = self.file(f"bids/{self.bid_id}/x.docx")
        token = set_current_tenant(self.tenants["b"])
        try:
            with self.assertRaises(service.ProjectDeletionError) as caught:
                service.delete_project(self.project.id)
            self.assertEqual(caught.exception.status_code, 404)
            clone = self.project.model_copy(deep=True)
            storage.save_project(clone)
            bid_b = storage.get_bid_id(clone.id)
            other_tenant = self.file(f"bids/{bid_b}/x.docx")
            service.delete_project(clone.id)
            self.assertFalse(other_tenant.exists())
        finally:
            reset_current_tenant(token)
        self.assertTrue(original.exists())
        self.assertEqual(storage.load_project(self.project.id).id, self.project.id)

    def test_request_and_persisted_running_task_refuse_delete(self):
        own = self.file(f"bids/{self.bid_id}/x.docx")
        with project_request(self.project.id):
            with self.assertRaises(service.ProjectDeletionError) as caught:
                service.delete_project(self.project.id)
            self.assertEqual(caught.exception.status_code, 409)
        self.insert("bid_parse_info", bid_id=self.bid_id, parse_status="processing")
        with self.assertRaises(service.ProjectDeletionError) as caught:
            service.delete_project(self.project.id)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertTrue(own.exists())
        self.assertEqual(storage.load_project(self.project.id).id, self.project.id)

    def test_database_failure_restores_staged_files(self):
        own = self.file(f"bids/{self.bid_id}/x.docx")
        self.sql("CREATE TRIGGER deny_project_delete BEFORE DELETE ON projects BEGIN SELECT RAISE(ABORT, 'offline fail'); END")
        with self.assertRaises(service.ProjectDeletionError) as caught:
            service.delete_project(self.project.id)
        self.assertEqual(caught.exception.status_code, 500)
        self.assertTrue(own.is_file())
        self.assertEqual(storage.load_project(self.project.id).id, self.project.id)
        self.assertFalse(self.sql("SELECT 1 FROM project_deletions"))

    def test_cleanup_failure_is_committed_success_warning_and_retry(self):
        own = self.file(f"bids/{self.bid_id}/x.docx")
        with patch.object(service.shutil, "rmtree", side_effect=PermissionError("offline locked file")):
            result = service.delete_project(self.project.id)
        self.assertTrue(result["success"])
        self.assertEqual(result["cleanup"]["status"], "pending")
        self.assertTrue(result["warnings"])
        self.assertFalse(own.exists())
        self.assertFalse(self.sql("SELECT 1 FROM projects WHERE id = ?", (self.project.id,)))
        result = service.delete_project(self.project.id)
        self.assertEqual(result["cleanup"]["status"], "completed")
        self.assertFalse(result["warnings"])
        self.assertIsNone(self.sql("SELECT cleanup_path FROM project_deletions WHERE project_id = ?", (self.project.id,))[0][0])

    def test_bad_legacy_path_refuses_before_mutation(self):
        external = self.root / "external.docx"
        external.write_bytes(b"original")
        payload = self.project.model_dump()
        payload["files"] = [{"fileId": "../external.docx"}]
        self.sql("UPDATE projects SET state_json = ? WHERE id = ?", (json.dumps(payload, default=str), self.project.id))
        with self.assertRaises(service.ProjectDeletionError) as caught:
            service.delete_project(self.project.id)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(external.read_bytes(), b"original")
        self.assertTrue(self.sql("SELECT 1 FROM projects WHERE id = ?", (self.project.id,)))

    def test_symlink_or_junction_is_not_followed(self):
        outside = self.root / "outside"
        outside.mkdir()
        original = outside / "original.docx"
        original.write_bytes(b"keep")
        link = settings.upload_dir / "bids" / str(self.bid_id)
        link.parent.mkdir(parents=True, exist_ok=True)
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            import subprocess
            result = subprocess.run([r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe", "-NoProfile", "-Command", "$ErrorActionPreference = 'Stop'; New-Item -ItemType Junction -Path '" + str(link).replace("'", "''") + "' -Target '" + str(outside).replace("'", "''") + "' | Out-Null"], capture_output=True, timeout=15)
            if result.returncode:
                self.skipTest("This host cannot create a junction for the offline test")
        try:
            with self.assertRaises(service.ProjectDeletionError) as caught:
                service.delete_project(self.project.id)
            self.assertEqual(caught.exception.status_code, 409)
            self.assertEqual(original.read_bytes(), b"keep")
        finally:
            # Remove only the verified test link itself, never its target.
            if link.is_symlink():
                link.unlink()
            else:
                link.rmdir()

    def test_staging_restore_failure_reports_manual_recovery(self):
        first = self.file(f"bids/{self.bid_id}/a.docx")
        second = self.file(f"bids/{self.bid_id}/b.docx")
        original_replace = Path.replace
        count = 0
        def failure(path, target):
            nonlocal count
            count += 1
            if count >= 2:
                raise PermissionError("offline move and restore failure")
            return original_replace(path, target)
        with patch.object(Path, "replace", failure):
            with self.assertRaises(service.ProjectDeletionError) as caught:
                service.delete_project(self.project.id)
        self.assertIn("需要管理员恢复", caught.exception.message)
        self.assertTrue(self.sql("SELECT 1 FROM projects WHERE id = ?", (self.project.id,)))
        self.assertEqual(sum(path.exists() for path in (first, second)), 1)
        self.assertTrue(list((settings.upload_dir / ".project-deletions").glob("*/manifest.json")))


    def test_http_api_delete_contract_and_list_bid_mapping(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.api.routes.projects import router
        app = FastAPI()
        @app.middleware("http")
        async def tenant_scope(request, call_next):
            token = set_current_tenant(self.tenants["a"])
            try:
                return await call_next(request)
            finally:
                reset_current_tenant(token)
        app.include_router(router, prefix="/api")
        original_sqlite_connect = sqlite3.connect
        def thread_compatible_connection(*args, **kwargs):
            # The fixture closes every captured connection on its owner test thread.
            kwargs["check_same_thread"] = False
            return original_sqlite_connect(*args, **kwargs)
        with patch.object(sqlite3, "connect", side_effect=thread_compatible_connection), TestClient(app) as client:
            listed = client.get("/api/projects").json()["projects"]
            mapped = {item["id"]: item["bidId"] for item in listed}
            self.assertEqual(mapped, {self.project.id: self.bid_id, self.other.id: self.other_bid})
            with project_request(self.project.id):
                busy = client.delete(f"/api/projects/{self.project.id}")
            self.assertEqual(busy.status_code, 409)
            self.assertIn("detail", busy.json())
            deleted = client.delete(f"/api/projects/{self.project.id}")
            self.assertEqual(deleted.status_code, 200)
            self.assertTrue(deleted.json()["deleted"])
            self.assertEqual(deleted.json()["bidId"], self.bid_id)
            self.assertEqual(client.delete(f"/api/projects/{self.project.id}").status_code, 200)
            self.assertEqual(client.delete("/api/projects/does-not-exist").status_code, 404)
            remaining = client.get("/api/projects").json()["projects"]
            self.assertEqual([item["id"] for item in remaining], [self.other.id])


if __name__ == "__main__":
    unittest.main()
