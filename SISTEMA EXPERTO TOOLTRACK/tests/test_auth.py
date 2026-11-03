import os
import tempfile
import unittest

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from fastapi.routing import APIRoute
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import auth
import demandas_api
import main
import users_api
from models.inventory import Auditoria, Base, DemandaPlaneacion, Ubicacion, Usuario
from passwords import hash_password


class RouteCoverageTests(unittest.TestCase):
    def test_every_route_has_a_permission_rule(self):
        routes = {
            (method.upper(), path)
            for path, methods in main.app.openapi()["paths"].items()
            for method in methods
        }
        missing = sorted(routes - set(auth.ROUTE_RULES))
        stale = sorted(set(auth.ROUTE_RULES) - routes)
        self.assertEqual(missing, [], "Rutas sin regla de permiso (se rechazarían)")
        self.assertEqual(stale, [], "Reglas que ya no corresponden a ninguna ruta")

    def test_literal_routes_win_over_parameterized_ones(self):
        self.assertEqual(auth.match_rule("GET", "/workflow/programs/clients")[0], "/workflow/programs/clients")
        self.assertEqual(auth.match_rule("GET", "/workflow/programs/4I/NP-1")[0],
                         "/workflow/programs/{tipo_maquina}/{numero_parte}")
        self.assertEqual(auth.match_rule("GET", "/workflow/tools/movable")[0], "/workflow/tools/movable")
        self.assertEqual(auth.match_rule("POST", "/workflow/tools/D1/move")[0], "/workflow/tools/{tool_id}/move")
        self.assertEqual(auth.match_rule("GET", "/nope"), (None, None))
        self.assertEqual(auth.match_rule("DELETE", "/login"), (None, None))
        self.assertEqual(auth.match_rule("GET", "/users/x/extra"), (None, None))

    def test_rules_use_known_permissions(self):
        known = set(auth.ALL_PERMISSIONS)
        for rule in auth.ROUTE_RULES.values():
            if rule not in (auth.PUBLIC, auth.AUTHENTICATED):
                self.assertIn(rule, known)

    def test_role_boundaries(self):
        perms = auth.permissions_for
        self.assertIn("kitting.operate", perms("TOOLCRIB_OPERATOR"))
        self.assertNotIn("demand.manage", perms("TOOLCRIB_OPERATOR"))
        self.assertNotIn("demand.mark", perms("TOOLCRIB_OPERATOR"))
        self.assertNotIn("inventory.edit", perms("TOOLCRIB_OPERATOR"))
        self.assertIn("demand.progress", perms("TOOLCRIB_OPERATOR"))
        self.assertIn("demand.manage", perms("PLANNING"))
        for forbidden in ("kitting.operate", "tools.move", "inventory.edit", "maintenance.operate", "user.manage"):
            self.assertNotIn(forbidden, perms("PLANNING"))
        for forbidden in ("kitting.operate", "tools.move", "demand.manage", "program.manage_not_real"):
            self.assertNotIn(forbidden, perms("ENGINEERING"))
        self.assertIn("program.manage", perms("ENGINEERING"))
        self.assertNotIn("demand.manage", perms("TOOLCRIB_SUPERVISOR"))
        self.assertIn("maintenance.operate", perms("MAINTENANCE_TECHNICIAN"))
        self.assertNotIn("demand.view", perms("MAINTENANCE_TECHNICIAN"))
        self.assertIn("user.manage", perms("SYSTEM_ADMIN"))
        self.assertEqual(perms("UNKNOWN"), frozenset())

    def test_role_normalization(self):
        self.assertEqual(auth.normalize_role("admin"), "SYSTEM_ADMIN")
        self.assertEqual(auth.normalize_role("operator"), "TOOLCRIB_OPERATOR")
        self.assertEqual(auth.normalize_role(None), "TOOLCRIB_OPERATOR")
        self.assertEqual(auth.normalize_role("planning"), "PLANNING")
        self.assertIsNone(auth.normalize_role("root"))


class TempDbCase(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.engine = create_engine(f"sqlite:///{self.path}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.originals = [(m, m.SessionLocal) for m in (auth, users_api, demandas_api)]
        for module, _ in self.originals:
            module.SessionLocal = self.factory
        auth._login_failures.clear()
        db = self.factory()
        db.add_all([
            Usuario(username="boss", password=hash_password("secret1"), role="SYSTEM_ADMIN", activo=True),
            Usuario(username="op", password=hash_password("secret2"), role="TOOLCRIB_OPERATOR", activo=True),
            Usuario(username="plan", password=hash_password("secret3"), role="PLANNING", activo=True),
        ])
        db.commit()
        db.close()
        self.client = TestClient(main.app)

    def tearDown(self):
        for module, original in self.originals:
            module.SessionLocal = original
        self.engine.dispose()
        os.remove(self.path)

    def login(self, username, password):
        response = self.client.post("/login", json={"username": username, "password": password})
        return response

    def headers(self, username, password):
        token = self.login(username, password).json()["token"]
        return {"Authorization": f"Bearer {token}"}


class AuthFlowTests(TempDbCase):
    def test_web_app_files_do_not_need_a_session_but_the_api_does(self):
        self.assertIn(self.client.get("/").status_code, (200, 404))
        self.assertEqual(self.client.get("/assets/nothing.js").status_code, 404)
        self.assertEqual(self.client.get("/assetsx").status_code, 403)
        self.assertEqual(self.client.get("/workflow/demandas/board").status_code, 401)

    def test_login_returns_token_role_and_permissions(self):
        data = self.login("boss", "secret1").json()
        self.assertEqual(data["role"], "SYSTEM_ADMIN")
        self.assertTrue(data["token"])
        self.assertIn("user.manage", data["permissions"])

    def test_token_is_stored_hashed(self):
        data = self.login("op", "secret2").json()
        db = self.factory()
        try:
            hashes = [row.token_hash for row in db.query(auth.Sesion).all()]
        finally:
            db.close()
        self.assertNotIn(data["token"], hashes)

    def test_requests_without_valid_token_are_rejected(self):
        self.assertEqual(self.client.get("/users").status_code, 401)
        self.assertEqual(self.client.get("/users", headers={"Authorization": "Bearer nope"}).status_code, 401)

    def test_wrong_password_and_inactive_user(self):
        self.assertEqual(self.login("op", "bad").status_code, 401)
        admin = self.headers("boss", "secret1")
        self.assertEqual(self.client.delete("/users/op", headers=admin).status_code, 200)
        self.assertEqual(self.login("op", "secret2").status_code, 401)

    def test_deactivating_user_kills_existing_session(self):
        op = self.headers("op", "secret2")
        self.assertEqual(self.client.get("/me", headers=op).status_code, 200)
        admin = self.headers("boss", "secret1")
        self.client.patch("/users/op", json={"activo": False}, headers=admin)
        self.assertEqual(self.client.get("/me", headers=op).status_code, 401)

    def test_logout_revokes_token(self):
        headers = self.headers("op", "secret2")
        self.assertEqual(self.client.post("/logout", headers=headers).status_code, 200)
        self.assertEqual(self.client.get("/me", headers=headers).status_code, 401)

    def test_lockout_after_repeated_failures(self):
        for _ in range(auth.LOGIN_MAX_FAILURES):
            self.assertEqual(self.login("op", "bad").status_code, 401)
        self.assertEqual(self.login("op", "secret2").status_code, 429)

    def test_role_blocks_forbidden_endpoints(self):
        op = self.headers("op", "secret2")
        self.assertEqual(self.client.get("/users", headers=op).status_code, 403)
        self.assertEqual(self.client.post("/workflow/demandas", headers=op, json={
            "maquina_codigo": "T-101", "numero_parte": "X", "cantidad": 1, "creado_por": "op"}).status_code, 403)
        plan = self.headers("plan", "secret3")
        self.assertEqual(self.client.post("/workflow/kits", headers=plan, json={}).status_code, 403)

    def test_denied_access_is_audited(self):
        op = self.headers("op", "secret2")
        self.client.get("/users", headers=op)
        db = self.factory()
        try:
            rows = db.query(Auditoria).filter(Auditoria.accion == "ACCESS_DENIED").all()
        finally:
            db.close()
        self.assertEqual([r.usuario for r in rows], ["op"])


class UserManagementTests(TempDbCase):
    def test_last_system_admin_is_protected(self):
        admin = self.headers("boss", "secret1")
        self.assertEqual(self.client.patch("/users/boss", json={"role": "PLANNING"}, headers=admin).status_code, 400)
        self.assertEqual(self.client.delete("/users/boss", headers=admin).status_code, 400)

    def test_second_admin_allows_demoting_first(self):
        admin = self.headers("boss", "secret1")
        self.assertEqual(self.client.patch("/users/op", json={"role": "SYSTEM_ADMIN"}, headers=admin).status_code, 200)
        self.assertEqual(self.client.patch("/users/boss", json={"role": "PLANNING"}, headers=admin).status_code, 200)

    def test_create_user_validates_role_and_password(self):
        admin = self.headers("boss", "secret1")
        bad_role = self.client.post("/users", headers=admin, json={"username": "n1", "password": "abcdef", "role": "root"})
        short = self.client.post("/users", headers=admin, json={"username": "n2", "password": "abc", "role": "PLANNING"})
        ok = self.client.post("/users", headers=admin, json={"username": "N3", "password": "abcdef", "role": "ENGINEERING"})
        self.assertEqual((bad_role.status_code, short.status_code, ok.status_code), (400, 400, 200))
        self.assertEqual(ok.json()["username"], "n3")
        self.assertEqual(self.login("n3", "abcdef").json()["role"], "ENGINEERING")

    def test_role_change_without_password(self):
        admin = self.headers("boss", "secret1")
        response = self.client.patch("/users/op", json={"role": "MAINTENANCE_TECHNICIAN"}, headers=admin)
        self.assertEqual(response.json()["role"], "MAINTENANCE_TECHNICIAN")
        self.assertEqual(self.login("op", "secret2").status_code, 200)

    def test_audit_records_old_and_new_values_without_password(self):
        admin = self.headers("boss", "secret1")
        self.client.patch("/users/op", json={"role": "ENGINEERING", "password": "newsecret"}, headers=admin)
        db = self.factory()
        try:
            row = db.query(Auditoria).filter(
                Auditoria.entidad == "usuarios", Auditoria.entidad_id == "op", Auditoria.accion.like("%UPDATE%")
            ).one()
        finally:
            db.close()
        self.assertEqual(row.usuario, "boss")
        self.assertEqual(row.rol, "SYSTEM_ADMIN")
        self.assertIn("TOOLCRIB_OPERATOR", row.antes)
        self.assertIn("ENGINEERING", row.despues)
        self.assertNotIn("pbkdf2", (row.antes or "") + (row.despues or ""))
        self.assertNotIn("newsecret", (row.antes or "") + (row.despues or ""))


class ActorStampingTests(TempDbCase):
    def test_actor_fields_come_from_token_not_from_client(self):
        app = FastAPI()

        @app.post("/echo")
        def echo(payload: dict):
            return payload

        app.add_middleware(auth.AuthMiddleware)
        auth.ROUTE_RULES[("POST", "/echo")] = "kitting.operate"
        try:
            token = self.login("op", "secret2").json()["token"]
            response = TestClient(app).post(
                "/echo", headers={"Authorization": f"Bearer {token}"},
                json={"realizado_por": "someone-else", "usuario": "x", "creado_por": "y", "note": "n"},
            )
        finally:
            del auth.ROUTE_RULES[("POST", "/echo")]
        self.assertEqual(response.json(), {"realizado_por": "op", "usuario": "op", "creado_por": "op", "note": "n"})


class DemandFieldPermissionTests(TempDbCase):
    def setUp(self):
        super().setUp()
        db = self.factory()
        db.add(Ubicacion(codigo="T-101", nombre="T-101", tipo="machine", activa=True))
        db.flush()
        now = auth.utcnow()
        db.add(DemandaPlaneacion(
            maquina_codigo="T-101", numero_parte="NP1", cantidad=100, piezas_producidas=0, orden=1,
            activa=True, creado_por="plan", creado_en=now, actualizado_en=now,
        ))
        db.commit()
        db.close()

    def patch(self, role, **fields):
        token = auth.acting_as("tester", role)
        try:
            return demandas_api.actualizar_demanda(1, demandas_api.DemandaUpdate(**fields))
        finally:
            auth.CURRENT_USER.reset(token)

    def test_operator_can_update_progress_only(self):
        self.assertTrue(self.patch("TOOLCRIB_OPERATOR", piezas_producidas=40)["ok"])
        with self.assertRaises(HTTPException) as caught:
            self.patch("TOOLCRIB_OPERATOR", cantidad=50)
        self.assertEqual(caught.exception.status_code, 403)
        with self.assertRaises(HTTPException) as caught:
            self.patch("TOOLCRIB_OPERATOR", marca="URGENTE", comentario="x")
        self.assertEqual(caught.exception.status_code, 403)

    def test_planning_changes_quantity_and_marks_but_not_progress(self):
        self.assertTrue(self.patch("PLANNING", cantidad=200)["ok"])
        self.assertTrue(self.patch("PLANNING", marca="URGENTE", comentario="cliente")["ok"])
        with self.assertRaises(HTTPException) as caught:
            self.patch("PLANNING", piezas_producidas=10)
        self.assertEqual(caught.exception.status_code, 403)

    def test_produced_cannot_exceed_quantity(self):
        with self.assertRaises(HTTPException) as caught:
            self.patch("TOOLCRIB_OPERATOR", piezas_producidas=101)
        self.assertEqual(caught.exception.status_code, 400)
        self.patch("TOOLCRIB_OPERATOR", piezas_producidas=60)
        with self.assertRaises(HTTPException) as caught:
            self.patch("PLANNING", cantidad=50)
        self.assertEqual(caught.exception.status_code, 400)

    def test_quantity_limit(self):
        with self.assertRaises(HTTPException):
            self.patch("PLANNING", cantidad=demandas_api.MAX_CANTIDAD + 1)

    def test_create_normalizes_part_and_rejects_inactive_machine(self):
        db = self.factory()
        db.add(Ubicacion(codigo="T-102", nombre="T-102", tipo="machine", activa=False))
        db.commit()
        db.close()
        with self.assertRaises(HTTPException) as caught:
            demandas_api.crear_demanda(demandas_api.DemandaCreate(
                maquina_codigo="T-102", numero_parte="np1", cantidad=5, creado_por="x"))
        self.assertIn(caught.exception.status_code, (400, 404))

    def test_without_authenticated_user_patch_is_refused(self):
        with self.assertRaises(HTTPException) as caught:
            demandas_api.actualizar_demanda(1, demandas_api.DemandaUpdate(piezas_producidas=1))
        self.assertEqual(caught.exception.status_code, 401)


class OpenKitIndexTests(unittest.TestCase):
    def test_database_blocks_second_open_kit_for_machine_or_cart(self):
        import sqlite3
        handle, path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        try:
            conn = sqlite3.connect(path)
            conn.execute("CREATE TABLE surtidos (id TEXT PRIMARY KEY, maquina_codigo TEXT, carro_codigo TEXT, estado TEXT)")
            conn.commit()
            conn.close()
            main.ensure_unique_open_kits(path)
            conn = sqlite3.connect(path)
            conn.execute("INSERT INTO surtidos VALUES ('a', 'T-101', 'C1', 'preparing')")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO surtidos VALUES ('b', 'T-101', 'C2', 'ready')")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO surtidos VALUES ('c', 'T-102', 'C1', 'paused')")
            conn.execute("UPDATE surtidos SET estado='delivered' WHERE id='a'")
            conn.execute("INSERT INTO surtidos VALUES ('d', 'T-101', 'C1', 'preparing')")
            conn.commit()
            conn.close()
        finally:
            os.remove(path)


if __name__ == "__main__":
    unittest.main()
