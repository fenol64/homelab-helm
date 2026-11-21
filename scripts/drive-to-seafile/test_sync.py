#!/usr/bin/env python3
"""Testes do plano de cópia. Não falam com o Drive nem com o Seafile."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sync import (
    FOLDER,
    SHORTCUT,
    DriveItem,
    Plan,
    QuotaExceeded,
    SkipFile,
    Upload,
    UploadRejected,
    build_plan,
    destination_filename,
    execute,
    format_bytes,
    is_excluded_name,
    item_from_api,
    load_state,
    normalize_upload_url,
    sanitize_filename,
    state_key,
)

DOC = "application/vnd.google-apps.document"
SHEET = "application/vnd.google-apps.spreadsheet"
FORM = "application/vnd.google-apps.form"
TEXT = "text/plain"


def item(file_id, name, mime, parents, **kwargs) -> DriveItem:
    return DriveItem(
        id=file_id,
        name=name,
        mime_type=mime,
        parents=tuple(parents),
        **kwargs,
    )


def sample_drive() -> dict[str, DriveItem]:
    return {
        "ssd": item("ssd", "backup ssd", FOLDER, ["root"]),
        "foto": item("foto", "foto.jpg", "image/jpeg", ["ssd"], size=10, modified_time="t1"),
        "sub": item("sub", "sub", FOLDER, ["ssd"]),
        "a": item("a", "a.txt", TEXT, ["sub"], size=1, modified_time="t1"),
        "docs": item("docs", "Documentos", FOLDER, ["root"]),
        "nota": item("nota", "nota", DOC, ["docs"], modified_time="t2"),
        "nested": item("nested", "Backup  SSD", FOLDER, ["docs"]),
        "segredo": item("segredo", "segredo.txt", TEXT, ["nested"], size=3, modified_time="t3"),
        "ok": item("ok", "ok.txt", TEXT, ["docs"], size=4, modified_time="t4"),
        "solto": item("solto", "solto.pdf", "application/pdf", ["root"], size=5, modified_time="t5"),
        "sheet": item("sheet", "planilha", SHEET, ["root"], modified_time="t6"),
        "arquivo_ssd": item("arquivo_ssd", "backup ssd", TEXT, ["docs"], size=2, modified_time="t7"),
        "both": item("both", "both.txt", TEXT, ["ssd", "docs"], size=8, modified_time="t8"),
        "atalho": item(
            "atalho",
            "atalho segredo",
            SHORTCUT,
            ["root"],
            target_id="segredo",
            target_mime=TEXT,
        ),
        "atalho2": item(
            "atalho2",
            "atalho ok",
            SHORTCUT,
            ["root"],
            target_id="ok",
            target_mime=TEXT,
        ),
        "atalho_pasta": item(
            "atalho_pasta",
            "atalho pasta",
            SHORTCUT,
            ["root"],
            target_id="docs",
            target_mime=FOLDER,
        ),
        "externo": item("externo", "externo.txt", TEXT, ["root"], size=9, modified_time="t9", listed=False),
        "atalho_ext": item(
            "atalho_ext",
            "atalho externo",
            SHORTCUT,
            ["root"],
            target_id="externo",
            target_mime=TEXT,
        ),
        "form": item("form", "pesquisa", FORM, ["root"]),
        "orfao": item("orfao", "so compartilhado.txt", TEXT, []),
    }


class PlanTest(unittest.TestCase):
    def test_exclude_matches_folder_name_loosely(self) -> None:
        self.assertTrue(is_excluded_name("Backup  SSD", ["backup ssd"]))
        self.assertFalse(is_excluded_name("backup-ssd", ["backup ssd"]))
        self.assertFalse(is_excluded_name("backup ssd extra", ["backup ssd"]))

    def test_backup_ssd_and_its_contents_stay_out(self) -> None:
        plan = build_plan(sample_drive(), "root", ["backup ssd"], {})
        paths = [upload.path for upload in plan.uploads]
        self.assertEqual(
            paths,
            [
                "/Documentos/backup ssd",
                "/Documentos/both.txt",
                "/Documentos/nota.docx",
                "/Documentos/ok.txt",
                "/atalho externo",
                "/atalho ok",
                "/planilha.xlsx",
                "/solto.pdf",
            ],
        )
        self.assertEqual(plan.mkdirs, ["/Documentos"])
        self.assertFalse(any(path.startswith("/backup ssd") for path in paths))
        self.assertFalse(any("segredo" in path or "foto" in path or "externo.txt" in path for path in paths))
        self.assertEqual(plan.orphans, 1)
        reasons = {skip.path: skip.reason for skip in plan.skips}
        self.assertEqual(reasons["/backup ssd"], "pasta excluída")
        self.assertEqual(reasons["/Documentos/Backup  SSD"], "pasta excluída")
        self.assertEqual(reasons["/atalho segredo"], "atalho para a pasta excluída")
        self.assertEqual(reasons["/atalho pasta"], "atalho de pasta")
        self.assertEqual(reasons["/pesquisa"], "formulário do Google")

        by_path = {upload.path: upload for upload in plan.uploads}
        self.assertEqual(by_path["/atalho ok"].drive_id, "ok")
        self.assertIsNone(by_path["/atalho ok"].export_mime)
        self.assertEqual(
            by_path["/Documentos/nota.docx"].export_mime,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        self.assertEqual(by_path["/Documentos/nota.docx"].drive_id, "nota")

    def test_same_file_is_skipped_until_it_changes(self) -> None:
        synced = {state_key("solto", "/solto.pdf"): {"modifiedTime": "t5"}}
        plan = build_plan(sample_drive(), "root", ["backup ssd"], synced)
        paths = [upload.path for upload in plan.uploads]
        self.assertNotIn("/solto.pdf", paths)
        self.assertTrue(any(skip.path == "/solto.pdf" and skip.reason == "já sincronizado" for skip in plan.skips))

        changed = {state_key("solto", "/solto.pdf"): {"modifiedTime": "antigo"}}
        again = build_plan(sample_drive(), "root", ["backup ssd"], changed)
        self.assertIn("/solto.pdf", [upload.path for upload in again.uploads])

    def test_without_exclude_the_folder_is_copied(self) -> None:
        plan = build_plan(sample_drive(), "root", [], {})
        paths = {upload.path for upload in plan.uploads}
        self.assertIn("/backup ssd/foto.jpg", paths)
        self.assertIn("/backup ssd/sub/a.txt", paths)
        self.assertIn("/Documentos/Backup  SSD/segredo.txt", paths)

    def test_root_alias_and_duplicate_names(self) -> None:
        items = {
            "a": item("a", "mesmo.txt", TEXT, ["root"], modified_time="1"),
            "b": item("b", "mesmo.txt", TEXT, ["root"], modified_time="2"),
        }
        plan = build_plan(items, "REAL", ["backup ssd"], {})
        self.assertEqual([upload.path for upload in plan.uploads], ["/mesmo.txt", "/mesmo (2).txt"])

    def test_names(self) -> None:
        self.assertEqual(destination_filename("nota", DOC), "nota.docx")
        self.assertEqual(destination_filename("nota.DOCX", DOC), "nota.DOCX")
        self.assertIsNone(destination_filename("pesquisa", FORM))
        self.assertEqual(destination_filename("foto.jpg", "image/jpeg"), "foto.jpg")
        self.assertEqual(sanitize_filename("a/b:c"), "a_b_c")
        self.assertEqual(sanitize_filename(" .. "), "_")
        self.assertLessEqual(len(sanitize_filename("á" * 300).encode("utf-8")), 200)
        self.assertEqual(format_bytes(1536), "1.5 KiB")

    def test_api_record(self) -> None:
        parsed = item_from_api(
            {
                "id": "1",
                "name": "atalho",
                "mimeType": SHORTCUT,
                "size": "15",
                "parents": ["root"],
                "shortcutDetails": {"targetId": "2", "targetMimeType": TEXT},
            }
        )
        self.assertEqual(parsed.target_id, "2")
        self.assertEqual(parsed.size, 15)
        self.assertTrue(parsed.is_shortcut)

    def test_upload_link_from_inside_the_cluster_uses_the_public_host(self) -> None:
        server = "https://drive.taileba3bd.ts.net"
        self.assertEqual(
            normalize_upload_url("http://127.0.0.1:8082/upload-api/abc", server),
            "https://drive.taileba3bd.ts.net/seafhttp/upload-api/abc",
        )
        self.assertEqual(
            normalize_upload_url('"http://192.168.0.42:8082/upload-api/abc"', server),
            "https://drive.taileba3bd.ts.net/seafhttp/upload-api/abc",
        )
        self.assertEqual(
            normalize_upload_url("http://10.1.2.3/seafhttp/upload-api/abc", server),
            "https://drive.taileba3bd.ts.net/seafhttp/upload-api/abc",
        )
        public = "https://drive.taileba3bd.ts.net/seafhttp/upload-api/abc"
        self.assertEqual(normalize_upload_url(public, server), public)

    def test_state_from_another_library_is_not_reused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text(
                json.dumps({"version": 1, "server": "https://a", "repo": "outra", "files": {"k": {}}}),
                encoding="utf-8",
            )
            state = load_state(path, "https://a", "Google Drive")
            self.assertEqual(state["files"], {})
            path.write_text("{}", encoding="utf-8")
            with self.assertRaises(SystemExit):
                load_state(path, "https://a", "Google Drive")


class ExecuteTest(unittest.TestCase):
    def test_one_failure_does_not_stop_the_rest(self) -> None:
        bad = Upload("b", "/ruim.txt", "ruim.txt", "/", None, "t", 1, None)
        skipped = Upload("s", "/grande.docx", "grande.docx", "/", "application/pdf", "t", None, None)
        good = Upload("g", "/ok.txt", "ok.txt", "/", None, "t", 2, "abc")
        plan = Plan(uploads=[bad, skipped, good])
        state: dict = {"version": 1, "files": {}}
        saved: list[int] = []

        def download(action: Upload, dest: Path) -> None:
            if action.drive_id == "s":
                raise SkipFile("o Drive não exporta este arquivo (limite de 10 MB)")
            dest.write_bytes(b"dados")

        def upload(action: Upload, dest: Path) -> None:
            if action.drive_id == "b":
                raise UploadRejected("440: nome")
            self.assertEqual(dest.read_bytes(), b"dados")

        with tempfile.TemporaryDirectory() as directory:
            counts = execute(
                plan,
                state,
                download,
                upload,
                workdir=Path(directory),
                on_saved=lambda: saved.append(1),
            )
        self.assertEqual(counts, {"enviados": 1, "erros": 1, "pulados": 1})
        self.assertEqual(saved, [1])
        self.assertIn(state_key("g", "/ok.txt"), state["files"])
        self.assertNotIn(state_key("b", "/ruim.txt"), state["files"])

    def test_limit_and_quota(self) -> None:
        first = Upload("1", "/a.txt", "a.txt", "/", None, "t", 1, None)
        second = Upload("2", "/b.txt", "b.txt", "/", None, "t", 1, None)
        plan = Plan(uploads=[first, second])
        state: dict = {"files": {}}

        def download(action: Upload, dest: Path) -> None:
            dest.write_bytes(b"a")

        def upload(action: Upload, dest: Path) -> None:
            if action.drive_id == "2":
                raise QuotaExceeded("cheio")

        with tempfile.TemporaryDirectory() as directory:
            counts = execute(
                plan,
                state,
                download,
                upload,
                workdir=Path(directory),
                limit=1,
            )
        self.assertEqual(counts["enviados"], 1)
        self.assertNotIn(state_key("2", "/b.txt"), state["files"])

        state = {"files": {}}
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(QuotaExceeded):
                execute(plan, state, download, upload, workdir=Path(directory))
        self.assertIn(state_key("1", "/a.txt"), state["files"])


if __name__ == "__main__":
    unittest.main()
