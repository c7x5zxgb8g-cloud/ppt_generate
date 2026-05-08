import tempfile
import unittest
from pathlib import Path

import webapp.app as webapp_app


class WebappProjectDeleteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.original_paths = {
            "REPO_ROOT": webapp_app.REPO_ROOT,
            "WEBAPP_DIR": webapp_app.WEBAPP_DIR,
            "DATA_DIR": webapp_app.DATA_DIR,
            "UPLOAD_DIR": webapp_app.UPLOAD_DIR,
            "DB_PATH": webapp_app.DB_PATH,
        }
        webapp_app.REPO_ROOT = self.root
        webapp_app.WEBAPP_DIR = self.root / "webapp"
        webapp_app.DATA_DIR = webapp_app.WEBAPP_DIR / "data"
        webapp_app.UPLOAD_DIR = webapp_app.DATA_DIR / "uploads"
        webapp_app.DB_PATH = webapp_app.DATA_DIR / "ppt_master_web.sqlite3"
        self.app = webapp_app.create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def tearDown(self):
        for name, value in self.original_paths.items():
            setattr(webapp_app, name, value)
        self.tmp.cleanup()

    def login(self, user_id="user-1"):
        now = webapp_app.utc_now()
        webapp_app.execute(
            """
            INSERT INTO users (id, email, display_name, password_hash, role, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, f"{user_id}@example.com", "User", "hash", "user", now),
        )
        with self.client.session_transaction() as sess:
            sess["user_id"] = user_id
        return user_id

    def create_project_record(self, user_id, project_id="project-1", job_status="succeeded"):
        project_dir = self.root / "projects" / "web" / user_id / "deck"
        project_dir.mkdir(parents=True)
        project_dir.joinpath("metadata.json").write_text("{}", encoding="utf-8")
        now = webapp_app.utc_now()
        webapp_app.execute(
            """
            INSERT INTO projects (id, user_id, name, canvas_format, project_path, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (project_id, user_id, "Deck", "ppt169", str(project_dir), now, now),
        )
        webapp_app.execute(
            """
            INSERT INTO jobs (id, user_id, project_id, type, status, stage, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("job-1", user_id, project_id, "validate", job_status, "finished", now, now),
        )
        return project_dir

    def test_delete_project_removes_record_jobs_and_directory(self):
        user_id = self.login()
        project_dir = self.create_project_record(user_id)

        response = self.client.delete("/api/projects/project-1")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["deletedProjectId"], "project-1")
        self.assertFalse(project_dir.exists())
        self.assertIsNone(webapp_app.query_one("SELECT * FROM projects WHERE id = ?", ("project-1",)))
        self.assertIsNone(webapp_app.query_one("SELECT * FROM jobs WHERE id = ?", ("job-1",)))

    def test_delete_project_rejects_active_job(self):
        user_id = self.login()
        project_dir = self.create_project_record(user_id, job_status="running")

        response = self.client.delete("/api/projects/project-1")

        self.assertEqual(response.status_code, 409)
        self.assertTrue(project_dir.exists())
        self.assertIsNotNone(webapp_app.query_one("SELECT * FROM projects WHERE id = ?", ("project-1",)))
        self.assertIsNotNone(webapp_app.query_one("SELECT * FROM jobs WHERE id = ?", ("job-1",)))


if __name__ == "__main__":
    unittest.main()
