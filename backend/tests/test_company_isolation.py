"""Exercise company boundaries through the real HTTP authentication middleware."""
from __future__ import annotations

from contextlib import ExitStack
from concurrent.futures import ThreadPoolExecutor
import gc
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.tenant_context import TenantContext, reset_current_tenant, set_current_tenant
from app.main import app
from app.modules.knowledge import service as knowledge_service
from app.services import auth_service, mysql_compat, storage


class CompanyIsolationTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        test_root = Path(__file__).resolve().parents[2] / ".cache" / "company-isolation-tests"
        test_root.mkdir(parents=True, exist_ok=True)
        self.temp = self.stack.enter_context(tempfile.TemporaryDirectory(dir=test_root))
        self.stack.enter_context(patch.object(settings, "data_dir", Path(self.temp)))
        self.stack.enter_context(patch.object(settings, "database_path", None))
        self.stack.enter_context(patch.object(settings, "db_backend", "sqlite"))
        self.stack.enter_context(patch.object(settings, "admin_initial_username", "system_admin"))
        self.stack.enter_context(patch.object(settings, "admin_initial_password", "test-password-1234"))
        self.stack.enter_context(patch.object(auth_service, "_AUTH_SCHEMA_PATH", None))
        self.connections = []
        original_connect = storage.connect
        def tracked_connect():
            conn = original_connect()
            self.connections.append(conn)
            return conn
        self.stack.enter_context(patch.object(storage, "connect", side_effect=tracked_connect))
        self.client = TestClient(app)
        self.addCleanup(self.close_client_and_connections)
        self.admin = self.login("system_admin", "test-password-1234")
        self.companies = {}
        self.users = {}
        for suffix in ("a", "b"):
            company = self.request("POST", "/api/auth/admin/companies", self.admin,
                                   json={"company_name": f"Test Company {suffix}"})
            self.assertEqual(company.status_code, 200, company.text)
            self.companies[suffix] = company.json()["data"]
            user = self.request("POST", "/api/auth/admin/users", self.admin,
                                json={"username": f"test_user_{suffix}", "password": "user-password-1234",
                                      "display_name": suffix, "company_id": company.json()["data"]["company_id"]})
            self.assertEqual(user.status_code, 200, user.text)
            self.users[suffix] = self.login(f"test_user_{suffix}", "user-password-1234")

    def close_client_and_connections(self):
        self.client.close()
        self.connections.clear()
        gc.collect()

    def login(self, username, password):
        response = self.client.post("/api/auth/login", json={"username": username, "password": password})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["data"]["access_token"]

    def request(self, method, path, token, **kwargs):
        return self.client.request(method, path, headers={"Authorization": f"Bearer {token}"}, **kwargs)

    def create_project(self, suffix, title):
        response = self.request("POST", "/api/projects", self.users[suffix], json={"settings": {"bidTitle": title}})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_project_and_bid_id_boundaries(self):
        a = self.create_project("a", "Only A")
        b = self.create_project("b", "Only B")
        self.assertEqual(a["bidId"], b["bidId"])
        self.assertNotEqual(a["project"]["id"], b["project"]["id"])
        self.assertEqual(self.request("GET", "/api/projects/" + a["project"]["id"], self.admin).status_code, 404)
        a_id, b_id = a["project"]["id"], b["project"]["id"]
        for suffix, own_id, foreign_id, own_title in (("a", a_id, b_id, "Only A"), ("b", b_id, a_id, "Only B")):
            own = self.request("GET", f"/api/projects/{own_id}", self.users[suffix])
            self.assertEqual(own.status_code, 200, own.text)
            self.assertEqual(own.json()["project"]["settings"]["bidTitle"], own_title)
            self.assertEqual(self.request("GET", f"/api/projects/{foreign_id}", self.users[suffix]).status_code, 404)
            listed = self.request("GET", "/api/projects", self.users[suffix])
            self.assertEqual([item["id"] for item in listed.json()["projects"]], [own_id])
            self.assertEqual(self.request("DELETE", f"/api/projects/{foreign_id}", self.users[suffix]).status_code, 404)
        # The same numeric bid_id must resolve to each company's own record.
        for suffix, title in (("a", "Only A"), ("b", "Only B")):
            response = self.request("GET", f"/api/bid/{a['bidId']}/parse-info", self.users[suffix])
            self.assertEqual(response.status_code, 200, response.text)
        a2 = self.create_project("a", "Only A second")
        self.assertEqual(self.request("GET", f"/api/bid/{a2['bidId']}/parse-info", self.users["b"]).status_code, 404)

    def test_parallel_requests_do_not_share_company_context(self):
        self.create_project("a", "Parallel A")
        self.create_project("b", "Parallel B")
        def read(suffix):
            response = self.request("GET", "/api/projects", self.users[suffix])
            self.assertEqual(response.status_code, 200, response.text)
            return response.json()["projects"][0]["settings"]["bidTitle"]
        keys = ["a", "b"] * 10
        with ThreadPoolExecutor(max_workers=4) as executor:
            titles = list(executor.map(read, keys))
        self.assertEqual(titles, ["Parallel A" if key == "a" else "Parallel B" for key in keys])

    def test_model_choices_require_admin_and_stay_with_company(self):
        self.assertEqual(self.request("PUT", "/api/models/scenes/catalog", self.users["a"],
                                      json={"model": "qwen-plus"}).status_code, 403)
        admins = {}
        for suffix in ("a", "b"):
            username = f"model_admin_{suffix}"
            created = self.request("POST", "/api/auth/admin/users", self.admin,
                                   json={"username": username, "password": "model-password-1234",
                                         "display_name": username, "company_id": self.companies[suffix]["company_id"], "role": "admin"})
            self.assertEqual(created.status_code, 200, created.text)
            admins[suffix] = self.login(username, "model-password-1234")
        for suffix, model in (("a", "qwen-plus"), ("b", "qwen-max")):
            saved = self.request("PUT", "/api/models/scenes/catalog", admins[suffix], json={"model": model})
            self.assertEqual(saved.status_code, 200, saved.text)
            scenes = self.request("GET", "/api/models/scenes", self.users[suffix])
            self.assertEqual(scenes.status_code, 200, scenes.text)
            self.assertEqual(next(row["model"] for row in scenes.json()["scenes"] if row["scene"] == "catalog"), model)

    def test_uploads_are_private_even_with_known_paths(self):
        project = self.create_project("a", "Private upload")
        project_id = project["project"]["id"]
        upload = self.request("POST", f"/api/projects/{project_id}/files", self.users["a"],
                              files={"files": ("private.txt", b"company-a-secret", "text/plain")})
        self.assertEqual(upload.status_code, 200, upload.text)
        file_id = upload.json()["files"][0]["fileId"]
        relative = f"companies/{self.companies['a']['company_id']}/{file_id}"
        self.assertEqual(self.request("GET", f"/uploads/{relative}", self.users["a"]).content, b"company-a-secret")
        self.assertEqual(self.request("GET", f"/uploads/{relative}", self.users["b"]).status_code, 404)
        self.assertEqual(self.request("GET", f"/uploads/{relative}", self.admin).status_code, 404)
        self.assertEqual(self.request("POST", f"/api/projects/{project_id}/files", self.users["b"],
                                      files={"files": ("x.txt", b"x", "text/plain")}).status_code, 404)

    def test_knowledge_ids_and_content_are_company_local(self):
        item_ids = {}
        for suffix, title in (("a", "A private knowledge"), ("b", "B private knowledge")):
            user, company = auth_service.authenticate_token(self.users[suffix])
            tenant = auth_service.build_tenant_context(user, company)
            token = set_current_tenant(tenant)
            try:
                knowledge_service.ensure_schema()
                now = storage.now_utc().isoformat()
                with storage.connect() as conn:
                    cur = conn.execute("INSERT INTO knowledge_item (title,file_type,source_name,content_text,status,create_time,update_time) VALUES (?,?,?,?,?,?,?)",
                                       (title, "txt", title + ".txt", title, "completed", now, now))
                    conn.commit()
                    item_ids[suffix] = int(cur.lastrowid)
            finally:
                reset_current_tenant(token)
        self.assertEqual(item_ids["a"], item_ids["b"])
        for suffix, title in (("a", "A private knowledge"), ("b", "B private knowledge")):
            response = self.request("GET", f"/api/knowledge/items/{item_ids[suffix]}", self.users[suffix])
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["data"]["title"], title)
        user, company = auth_service.authenticate_token(self.users["a"])
        token = set_current_tenant(auth_service.build_tenant_context(user, company))
        try:
            with storage.connect() as conn:
                conn.execute("DELETE FROM knowledge_item WHERE item_id = ?", (item_ids["a"],))
                conn.commit()
        finally:
            reset_current_tenant(token)
        self.assertEqual(self.request("GET", f"/api/knowledge/items/{item_ids['a']}", self.users["a"]).status_code, 404)
        self.assertEqual(self.request("GET", f"/api/knowledge/items/{item_ids['b']}", self.users["b"]).status_code, 200)

    def test_company_disable_and_user_transfer_revoke_old_sessions(self):
        b = self.create_project("b", "Former B project")
        b_id = b["project"]["id"]
        disabled = self.request("PATCH", "/api/auth/admin/companies/" + self.companies["b"]["company_id"],
                                self.admin, json={"status": "disabled"})
        self.assertEqual(disabled.status_code, 200, disabled.text)
        self.assertEqual(self.request("GET", "/api/projects", self.users["b"]).status_code, 401)
        self.assertEqual(self.request("GET", "/api/projects", self.users["a"]).status_code, 200)
        enabled = self.request("PATCH", "/api/auth/admin/companies/" + self.companies["b"]["company_id"],
                               self.admin, json={"status": "active"})
        self.assertEqual(enabled.status_code, 200, enabled.text)
        self.assertEqual(self.request("GET", "/api/projects", self.users["b"]).status_code, 401)
        refreshed = self.login("test_user_b", "user-password-1234")
        self.assertEqual(self.request("GET", "/api/projects/" + b_id, refreshed).status_code, 200)
        users = self.request("GET", "/api/auth/admin/users", self.admin).json()["data"]
        user_id = next(user["user_id"] for user in users if user["username"] == "test_user_b")
        moved = self.request("PATCH", "/api/auth/admin/users/" + str(user_id), self.admin,
                             json={"company_id": self.companies["a"]["company_id"]})
        self.assertEqual(moved.status_code, 200, moved.text)
        self.assertEqual(self.request("GET", "/api/projects", refreshed).status_code, 401)
        transferred = self.login("test_user_b", "user-password-1234")
        self.assertEqual(self.request("GET", "/api/projects/" + b_id, transferred).status_code, 404)

    def test_only_system_admin_can_manage_companies_and_users(self):
        self.assertEqual(self.request("GET", "/api/auth/admin/companies", self.users["a"]).status_code, 403)
        company_admin = self.request("POST", "/api/auth/admin/users", self.admin,
                                     json={"username": "tenant_admin_a", "password": "admin-password-1234",
                                           "company_id": self.companies["a"]["company_id"], "role": "admin"})
        self.assertEqual(company_admin.status_code, 200, company_admin.text)
        token = self.login("tenant_admin_a", "admin-password-1234")
        self.assertEqual(self.request("GET", "/api/auth/admin/companies", token).status_code, 403)
        self.assertEqual(self.request("GET", "/api/auth/admin/users", token).status_code, 403)
        self.assertEqual(self.request("POST", "/api/auth/admin/companies", token, json={"company_name": "unauthorized"}).status_code, 403)
        self.assertEqual(self.request("GET", "/api/auth/admin/companies", self.admin).status_code, 200)


class MySQLTenantRoutingTests(unittest.TestCase):
    def test_company_ids_select_distinct_mysql_databases_without_a_server(self):
        names = []
        with patch.object(settings, "db_backend", "mysql"), \
             patch.object(settings, "mysql_database_prefix", "isolation_test"), \
             patch.object(storage, "_MYSQL_INITIALIZED_DATABASES", set()), \
             patch.object(storage, "init_db"), \
             patch.object(mysql_compat, "MySQLConnection", side_effect=lambda name: names.append(name) or name):
            for company_id in ("company_a", "company_b", "company_a"):
                tenant = TenantContext(1, "test", "company_user", company_id, company_id,
                                       Path("unused.sqlite"), Path("unused_uploads"))
                token = set_current_tenant(tenant)
                try:
                    self.assertEqual(storage.connect(), mysql_compat.database_name_for_company(company_id))
                finally:
                    reset_current_tenant(token)
        self.assertEqual(names, ["isolation_test_company_a", "isolation_test_company_b", "isolation_test_company_a"])



if __name__ == "__main__":
    unittest.main()
