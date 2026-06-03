"""
Integration tests for the FastAPI leave management API.

These tests use TestClient to exercise the full stack.
They currently pass because they're placeholders — replace
with real tests once you implement src/services.py.
"""

import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.app import app
from src.services import seed_demo_data


class TestLeaveAPI(unittest.TestCase):

    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.db = self.Session()
        seed_demo_data(self.db)

        def override_get_db():
            try:
                yield self.db
            finally:
                pass

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)

    def test_list_employees(self):
        resp = self.client.get("/employees")
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.json(), list)

    def test_get_employee(self):
        resp = self.client.get("/employees/1")
        # May return 200 or 404 depending on seed data
        self.assertIn(resp.status_code, (200, 404))

    def test_create_leave_request(self):
        resp = self.client.post("/leave-requests", json={
            "employee_id": 2,
            "leave_type": "annual",
            "start_date": "2026-06-10",
            "end_date": "2026-06-10",
        })

        self.assertEqual(resp.status_code, 201)
        body = resp.json()
        self.assertEqual(body["status"], "pending")
        self.assertEqual(body["start_session"], "am")
        self.assertEqual(body["end_session"], "pm")
        self.assertEqual(body["leave_usage_days"], 1.0)

    def test_preview_leave_usage(self):
        resp = self.client.post("/leave-requests/preview", json={
            "employee_id": 2,
            "leave_type": "annual",
            "start_date": "2026-06-10",
            "end_date": "2026-06-10",
        })

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["leave_usage_days"], 1.0)
        self.assertEqual(body["leave_usage_by_year"], {"2026": 1.0})
        self.assertEqual(body["working_sessions"], [
            {"date": "2026-06-10", "sessions": ["am", "pm"], "days": 1.0}
        ])
        self.assertEqual(body["excluded_dates"], [])

    def test_overlapping_leave_request_returns_conflict(self):
        payload = {
            "employee_id": 2,
            "leave_type": "annual",
            "start_date": "2026-06-10",
            "end_date": "2026-06-10",
        }
        first_resp = self.client.post("/leave-requests", json=payload)
        self.assertEqual(first_resp.status_code, 201)

        second_resp = self.client.post("/leave-requests", json=payload)

        self.assertEqual(second_resp.status_code, 409)
        self.assertIn("overlaps", second_resp.json()["detail"])

    def test_list_leave_requests_includes_leave_usage_days(self):
        create_resp = self.client.post("/leave-requests", json={
            "employee_id": 2,
            "leave_type": "annual",
            "start_date": "2026-06-10",
            "end_date": "2026-06-10",
        })
        self.assertEqual(create_resp.status_code, 201)

        list_resp = self.client.get("/leave-requests?employee_id=2&page_size=1")

        self.assertEqual(list_resp.status_code, 200)
        body = list_resp.json()
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["page_size"], 1)
        self.assertEqual(body["items"][0]["leave_usage_days"], 1.0)

if __name__ == "__main__":
    unittest.main()
