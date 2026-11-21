#!/usr/bin/env python3
"""Copia o Meu Drive para o Seafile, sem a pasta 'backup ssd'.

Não apaga nada no Drive nem no Seafile. Rodar de novo só reenvia o que mudou.
Arquivos soltos em "Compartilhados comigo" ficam de fora: entra o que está
no Meu Drive. Atalho de arquivo é copiado, a menos que o destino esteja só
dentro de uma pasta excluída. Atalho de pasta é ignorado.

Uso:
  python3 -m pip install -r requirements.txt
  python3 sync.py --dry-run
  SEAFILE_PASSWORD='...' python3 sync.py

Google Drive (uma vez):
  1. Ative a API em
     https://console.cloud.google.com/apis/library/drive.googleapis.com
  2. Crie um ID de cliente OAuth do tipo "App para computador".
  3. Baixe o JSON como credentials.json nesta pasta.
  4. Rode o script. O navegador pede acesso somente leitura.

Seafile, por variável de ambiente ou flag:
  SEAFILE_URL       padrão https://drive.taileba3bd.ts.net
                    na LAN: http://drive.192.168.0.42.sslip.io
  SEAFILE_USER      padrão admin@home.arpa
  SEAFILE_PASSWORD  senha atual. A do values-homelab.yaml só vale se
                    você não trocou depois do primeiro boot.
  SEAFILE_TOKEN     no lugar da senha, se você já tem um token da API
  SEAFILE_REPO      padrão "Google Drive". A biblioteca é criada se faltar.

Documentos, planilhas, apresentações e desenhos do Google viram
docx, xlsx, pptx e png. O Drive recusa exportar acima de 10 MB.
Formulário, site e mapa não têm arquivo e são ignorados.
"""

from __future__ import annotations

import argparse
import getpass
import ipaddress
import json
import os
import sys
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

FOLDER = "application/vnd.google-apps.folder"
SHORTCUT = "application/vnd.google-apps.shortcut"

# mime de exportação, sufixo acrescentado ao nome quando ainda não existe.
GOOGLE_EXPORTS: dict[str, tuple[str, str]] = {
    "application/vnd.google-apps.document": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".docx",
    ),
    "application/vnd.google-apps.spreadsheet": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xlsx",
    ),
    "application/vnd.google-apps.presentation": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".pptx",
    ),
    "application/vnd.google-apps.drawing": ("image/png", ".png"),
    "application/vnd.google-apps.script": (
        "application/vnd.google-apps.script+json",
        ".json",
    ),
    "application/vnd.google-apps.jam": ("application/pdf", ".pdf"),
}

UNEXPORTABLE = {
    "application/vnd.google-apps.form": "formulário do Google",
    "application/vnd.google-apps.map": "mapa do Google",
    "application/vnd.google-apps.site": "site do Google",
}

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_SERVER = "https://drive.taileba3bd.ts.net"
DEFAULT_USER = "admin@home.arpa"
DEFAULT_REPO = "Google Drive"
DEFAULT_EXCLUDE = "backup ssd"
DRIVE_FIELDS = (
    "id, name, mimeType, size, modifiedTime, md5Checksum, parents, trashed, "
    "shortcutDetails(targetId, targetMimeType)"
)


class SeafileError(Exception):
    pass


class QuotaExceeded(SeafileError):
    pass


class UploadRejected(SeafileError):
    pass


class SkipFile(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class DriveItem:
    id: str
    name: str
    mime_type: str
    parents: tuple[str, ...]
    size: int | None = None
    modified_time: str | None = None
    md5: str | None = None
    target_id: str | None = None
    target_mime: str | None = None
    listed: bool = True
    trashed: bool = False

    @property
    def is_folder(self) -> bool:
        return self.mime_type == FOLDER

    @property
    def is_shortcut(self) -> bool:
        return self.mime_type == SHORTCUT


@dataclass(frozen=True)
class Upload:
    drive_id: str
    path: str
    filename: str
    parent_dir: str
    export_mime: str | None
    modified_time: str | None
    size: int | None
    md5: str | None


@dataclass(frozen=True)
class Skip:
    path: str
    reason: str


@dataclass
class Plan:
    mkdirs: list[str] = field(default_factory=list)
    uploads: list[Upload] = field(default_factory=list)
    skips: list[Skip] = field(default_factory=list)
    orphans: int = 0


def norm_name(name: str) -> str:
    return " ".join(name.casefold().split())


def is_excluded_name(name: str, exclude_names: list[str]) -> bool:
    folded = norm_name(name)
    return folded in {norm_name(item) for item in exclude_names}


def sanitize_filename(name: str) -> str:
    cleaned = []
    for char in name:
        if char in '/\\:*?"<>|' or not char.isprintable():
            cleaned.append("_")
        else:
            cleaned.append(char)
    text = "".join(cleaned).strip().strip(".")
    if text in {"", ".", ".."}:
        text = "_"
    raw = text.encode("utf-8")
    if len(raw) > 200:
        text = raw[:200].decode("utf-8", errors="ignore").rstrip() or "_"
    return text


def export_of(mime: str) -> tuple[str, str] | None:
    return GOOGLE_EXPORTS.get(mime)


def destination_filename(name: str, mime: str) -> str | None:
    """Nome final no Seafile. None quando esse mime não vira arquivo."""
    if mime in UNEXPORTABLE or mime.startswith("application/vnd.google-apps.drive-sdk"):
        return None
    exported = export_of(mime)
    if exported is None and mime.startswith("application/vnd.google-apps."):
        return None
    suffix = exported[1] if exported else ""
    base = sanitize_filename(name)
    if suffix and not base.casefold().endswith(suffix.casefold()):
        base = sanitize_filename(base + suffix)
    return base


def api_path(path: str) -> str:
    if path in {"", "/"}:
        return "/"
    return "/" + path.strip("/")


def split_parent(path: str) -> tuple[str, str]:
    """'/Documentos/ok.txt' -> ('/Documentos', 'ok.txt')."""
    full = api_path(path)
    parent, _, name = full.rpartition("/")
    if parent == "":
        parent = "/"
    return parent, name


def parent_dir_form(path: str) -> str:
    """Diretório no formato que o upload do Seafile espera, com barra no fim."""
    directory = api_path(path)
    if directory == "/":
        return "/"
    return directory + "/"


def state_key(drive_id: str, path: str) -> str:
    return f"{drive_id}|{path}"


def _internal_host(hostname: str) -> bool:
    host = hostname.lower().rstrip(".")
    if host in {"localhost", "seafile", "127.0.0.1", "::1"}:
        return True
    if host.endswith(".svc") or host.endswith(".cluster.local") or ".svc." in host:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_private or address.is_loopback or address.is_link_local


def normalize_upload_url(upload_url: str, server: str) -> str:
    """Troca um link de upload interno (127.0.0.1, IP da LAN) pelo Seafile público.

    O container devolve o fileserver interno. De fora, o mesmo caminho fica em
    {servidor}/seafhttp/upload-api/...
    """
    text = upload_url.strip().strip('"')
    parsed = urllib.parse.urlparse(text)
    host = parsed.hostname or ""
    if not _internal_host(host):
        return text
    path = parsed.path or ""
    marker = "/seafhttp/"
    if marker in path:
        suffix = path[path.index("/seafhttp") :]
    else:
        suffix = path if path.startswith("/") else "/" + path
        suffix = "/seafhttp" + suffix
    query = f"?{parsed.query}" if parsed.query else ""
    return server.rstrip("/") + suffix + query


def _alias_root(items: dict[str, DriveItem], root_id: str) -> dict[str, DriveItem]:
    """A API às vezes devolve a pasta raiz como o apelido 'root'."""
    if root_id == "root":
        return items
    rewritten: dict[str, DriveItem] = {}
    for item in items.values():
        parents = tuple(root_id if parent == "root" else parent for parent in item.parents)
        if parents != item.parents:
            item = DriveItem(
                id=item.id,
                name=item.name,
                mime_type=item.mime_type,
                parents=parents,
                size=item.size,
                modified_time=item.modified_time,
                md5=item.md5,
                target_id=item.target_id,
                target_mime=item.target_mime,
                listed=item.listed,
                trashed=item.trashed,
            )
        rewritten[item.id] = item
    return rewritten


def _children_index(items: dict[str, DriveItem]) -> dict[str, list[DriveItem]]:
    """Só itens listados no Drive. O destino de um atalho entra no mapa para
    resolver o arquivo, mas não vira pasta extra na caminhada."""
    children: dict[str, list[DriveItem]] = {}
    for item in items.values():
        if not item.listed:
            continue
        for parent in item.parents:
            children.setdefault(parent, []).append(item)
    for group in children.values():
        group.sort(key=lambda item: (0 if item.is_folder else 1, norm_name(item.name), item.id))
    return children


def _excluded_folder_ids(items: dict[str, DriveItem], exclude_names: list[str]) -> set[str]:
    return {
        item.id
        for item in items.values()
        if item.is_folder and is_excluded_name(item.name, exclude_names)
    }


def _clean_path(
    item_id: str,
    items: dict[str, DriveItem],
    root_id: str,
    excluded_ids: set[str],
    memo: dict[str, bool],
) -> bool:
    """True se dá para chegar na raiz, ou num arquivo sem pasta, sem passar
    por uma pasta excluída. Atalho para dentro do backup ssd dá False."""
    if item_id in memo:
        return memo[item_id]
    memo[item_id] = False
    if item_id in excluded_ids:
        return False
    if item_id == root_id:
        memo[item_id] = True
        return True
    item = items.get(item_id)
    if item is None or not item.parents:
        memo[item_id] = True
        return True
    ok = any(_clean_path(parent, items, root_id, excluded_ids, memo) for parent in item.parents)
    memo[item_id] = ok
    return ok


def _blocked(
    item_id: str,
    items: dict[str, DriveItem],
    root_id: str,
    excluded_ids: set[str],
) -> bool:
    if item_id in excluded_ids:
        return True
    item = items.get(item_id)
    if item is None or not item.parents:
        return False
    return not _clean_path(item_id, items, root_id, excluded_ids, {})


def _allocate(used: set[str], filename: str) -> str:
    if filename not in used:
        used.add(filename)
        return filename
    if "." in filename and not filename.startswith("."):
        stem, ext = filename.rsplit(".", 1)
        ext = "." + ext
    else:
        stem, ext = filename, ""
    number = 2
    while True:
        candidate = f"{stem} ({number}){ext}"
        if candidate not in used:
            used.add(candidate)
            return candidate
        number += 1


def _join(directory: str, name: str) -> str:
    if directory == "/":
        return "/" + name
    return directory.rstrip("/") + "/" + name


def _source_item(item: DriveItem, items: dict[str, DriveItem]) -> DriveItem | None:
    if not item.is_shortcut:
        return item
    if item.target_id and item.target_id in items:
        target = items[item.target_id]
        if target.trashed:
            return None
        return target
    if item.target_id and item.target_mime:
        return DriveItem(
            id=item.target_id,
            name=item.name,
            mime_type=item.target_mime,
            parents=(),
            listed=False,
        )
    return None


def build_plan(
    items: dict[str, DriveItem],
    root_id: str,
    exclude_names: list[str],
    synced: dict[str, dict],
) -> Plan:
    items = _alias_root(items, root_id)
    plan = Plan()
    children = _children_index(items)
    excluded_ids = _excluded_folder_ids(items, exclude_names)

    reachable: set[str] = set()
    stack = [root_id]
    while stack:
        current = stack.pop()
        if current in reachable:
            continue
        reachable.add(current)
        stack.extend(child.id for child in children.get(current, ()))
    plan.orphans = sum(
        1 for item in items.values() if item.listed and item.id not in reachable
    )

    def walk(folder_id: str, directory: str, seen: set[str]) -> None:
        if folder_id in seen:
            return
        seen.add(folder_id)
        used: set[str] = set()
        for child in children.get(folder_id, ()):
            if child.is_folder:
                if is_excluded_name(child.name, exclude_names):
                    plan.skips.append(Skip(_join(directory, child.name), "pasta excluída"))
                    continue
                folder_name = sanitize_filename(child.name)
                folder_name = _allocate(used, folder_name)
                path = _join(directory, folder_name)
                plan.mkdirs.append(path)
                walk(child.id, path, seen)
                continue

            label = _join(directory, child.name)
            if child.is_shortcut and (
                child.target_mime == FOLDER
                or (child.target_id and child.target_id in excluded_ids)
            ):
                plan.skips.append(Skip(label, "atalho de pasta"))
                continue
            if child.is_shortcut and child.target_id and _blocked(
                child.target_id, items, root_id, excluded_ids
            ):
                plan.skips.append(Skip(label, "atalho para a pasta excluída"))
                continue

            source = _source_item(child, items)
            if source is None:
                plan.skips.append(Skip(label, "atalho sem destino"))
                continue
            if source.is_folder:
                plan.skips.append(Skip(label, "atalho de pasta"))
                continue

            filename = destination_filename(child.name, source.mime_type)
            if filename is None:
                reason = UNEXPORTABLE.get(source.mime_type, "tipo do Google sem arquivo")
                plan.skips.append(Skip(label, reason))
                continue
            filename = _allocate(used, filename)
            path = _join(directory, filename)
            parent, _ = split_parent(path)
            exported = export_of(source.mime_type)
            upload = Upload(
                drive_id=source.id,
                path=path,
                filename=filename,
                parent_dir=parent,
                export_mime=exported[0] if exported else None,
                modified_time=source.modified_time,
                size=source.size,
                md5=source.md5,
            )
            previous = synced.get(state_key(upload.drive_id, upload.path))
            if (
                upload.modified_time
                and previous
                and previous.get("modifiedTime") == upload.modified_time
            ):
                plan.skips.append(Skip(path, "já sincronizado"))
                continue
            plan.uploads.append(upload)

    walk(root_id, "/", set())
    return plan


def item_from_api(raw: dict, *, listed: bool = True) -> DriveItem:
    shortcut = raw.get("shortcutDetails") or {}
    size = raw.get("size")
    return DriveItem(
        id=raw["id"],
        name=raw.get("name") or "",
        mime_type=raw.get("mimeType") or "application/octet-stream",
        parents=tuple(raw.get("parents") or ()),
        size=int(size) if size is not None else None,
        modified_time=raw.get("modifiedTime"),
        md5=raw.get("md5Checksum"),
        target_id=shortcut.get("targetId"),
        target_mime=shortcut.get("targetMimeType"),
        listed=listed,
        trashed=bool(raw.get("trashed")),
    )


def load_state(path: Path, server: str, repo: str) -> dict:
    empty = {"version": 1, "server": server, "repo": repo, "files": {}}
    if not path.exists():
        return empty
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != 1 or "files" not in data:
        raise SystemExit(f"estado incompatível em {path}. Apague o arquivo ou escolha outro com --state.")
    if data.get("server") not in {None, server} or data.get("repo") not in {None, repo}:
        print(
            "aviso: o estado era de outro servidor ou outra biblioteca; este envio começa do zero",
            file=sys.stderr,
        )
        return empty
    data["server"] = server
    data["repo"] = repo
    return data


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def execute(
    plan: Plan,
    state: dict,
    download,
    upload,
    *,
    workdir: Path,
    limit: int | None = None,
    on_saved=None,
) -> dict[str, int]:
    """Baixa e envia. `download(upload, dest)` e `upload(upload, dest)` são
    injetados para o teste não falar com a rede. Um arquivo com erro não
    interrompe o resto. Sem espaço na biblioteca, para."""
    counts = {"enviados": 0, "erros": 0, "pulados": 0}
    sent = 0
    workdir.mkdir(parents=True, exist_ok=True)
    for action in plan.uploads:
        if limit is not None and sent >= limit:
            break
        temporary = workdir / ".part.download"
        try:
            download(action, temporary)
            upload(action, temporary)
        except SkipFile as exc:
            plan.skips.append(Skip(action.path, exc.reason))
            counts["pulados"] += 1
            print(f"pulado  {action.path}  ({exc.reason})", file=sys.stderr)
            continue
        except QuotaExceeded:
            raise
        except Exception as exc:
            counts["erros"] += 1
            print(f"erro    {action.path}  ({exc})", file=sys.stderr)
            continue
        finally:
            temporary.unlink(missing_ok=True)
        state["files"][state_key(action.drive_id, action.path)] = {
            "modifiedTime": action.modified_time,
            "size": action.size,
            "md5": action.md5,
            "path": action.path,
        }
        counts["enviados"] += 1
        sent += 1
        print(f"enviado {action.path}", file=sys.stderr)
        if on_saved is not None:
            on_saved()
    return counts


class SeafileClient:
    def __init__(self, base_url: str, token: str, repo_id: str) -> None:
        import requests

        self._requests = requests
        self.base = base_url.rstrip("/")
        self.repo_id = repo_id
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Token {token}"
        self._listings: dict[str, set[str]] = {}
        self._upload_links: dict[str, str] = {}

    def ensure_dir(self, path: str) -> None:
        directory = api_path(path)
        if directory == "/":
            return
        parent, name = split_parent(directory)
        self.ensure_dir(parent)
        if name in self._names(parent):
            return
        response = self.session.post(
            f"{self.base}/api2/repos/{self.repo_id}/dir/",
            params={"p": directory},
            data={"operation": "mkdir"},
            timeout=(30, 120),
        )
        if response.status_code not in {200, 201}:
            # Alguém criou no meio do caminho, ou a listagem veio vazia.
            if name not in self._names(parent, refresh=True):
                raise SeafileError(
                    f"não criei {directory} ({response.status_code}): {response.text[:300]}"
                )
        self._names(parent).add(name)

    def _names(self, path: str, refresh: bool = False) -> set[str]:
        directory = api_path(path)
        if directory in self._listings and not refresh:
            return self._listings[directory]
        response = self.session.get(
            f"{self.base}/api2/repos/{self.repo_id}/dir/",
            params={"p": directory},
            timeout=(30, 120),
        )
        if response.status_code == 404:
            names: set[str] = set()
        else:
            response.raise_for_status()
            body = response.json()
            entries = body if isinstance(body, list) else body.get("dirent_list") or []
            names = {entry["name"] for entry in entries if isinstance(entry, dict) and "name" in entry}
        self._listings[directory] = names
        return names

    def upload_file(self, parent_dir: str, filename: str, stream) -> None:
        parent = parent_dir_form(parent_dir)
        delay = 2.0
        last_error: Exception | None = None
        for attempt in range(4):
            try:
                stream.seek(0)
            except (AttributeError, OSError):
                pass
            link = self._upload_link(parent)
            separator = "&" if "?" in link else "?"
            try:
                response = self.session.post(
                    f"{link}{separator}ret-json=1",
                    data={"parent_dir": parent, "replace": "1"},
                    files={"file": (filename, stream, "application/octet-stream")},
                    timeout=(30, None),
                )
            except self._requests.RequestException as exc:
                last_error = exc
                self._upload_links.pop(api_path(parent_dir), None)
                if attempt == 3:
                    break
                time.sleep(delay)
                delay *= 2
                continue
            if response.status_code == 443:
                raise QuotaExceeded(response.text[:300] or "sem espaço na biblioteca")
            if response.status_code in {400, 440, 442}:
                raise UploadRejected(f"{response.status_code}: {response.text[:300]}")
            if response.status_code in {403, 500, 502, 503} and attempt < 3:
                self._upload_links.pop(api_path(parent_dir), None)
                time.sleep(delay)
                delay *= 2
                continue
            try:
                response.raise_for_status()
            except self._requests.HTTPError as exc:
                raise SeafileError(f"{response.status_code}: {response.text[:300]}") from exc
            return
        raise SeafileError(f"upload falhou: {last_error}")

    def _upload_link(self, parent_dir: str) -> str:
        directory = api_path(parent_dir)
        cached = self._upload_links.get(directory)
        if cached:
            return cached
        response = self.session.get(
            f"{self.base}/api2/repos/{self.repo_id}/upload-link/",
            params={"p": directory},
            timeout=(30, 120),
        )
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        url = response.json() if "json" in content_type else response.text
        if not isinstance(url, str):
            raise SeafileError(f"link de upload inesperado: {url!r}")
        normalized = normalize_upload_url(url, self.base)
        self._upload_links[directory] = normalized
        return normalized


def seafile_token(base_url: str, user: str, password: str) -> str:
    import requests

    response = requests.post(
        f"{base_url.rstrip('/')}/api2/auth-token/",
        data={"username": user, "password": password},
        timeout=(30, 120),
    )
    if response.status_code != 200:
        raise SeafileError(f"login no Seafile falhou ({response.status_code}): {response.text[:300]}")
    token = response.json().get("token")
    if not token:
        raise SeafileError("o Seafile não devolveu token")
    return token


def ensure_repo(base_url: str, token: str, name: str) -> str:
    import requests

    session = requests.Session()
    session.headers["Authorization"] = f"Token {token}"
    base = base_url.rstrip("/")
    response = session.get(f"{base}/api2/repos/", timeout=(30, 120))
    response.raise_for_status()
    body = response.json()
    repos = body if isinstance(body, list) else body.get("repos") or []
    matches = [repo for repo in repos if repo.get("name") == name]
    writable = [repo for repo in matches if "w" in str(repo.get("permission") or "rw")]
    chosen = (writable or matches or [None])[0]
    if chosen:
        if len(matches) > 1:
            print(
                f"aviso: {len(matches)} bibliotecas chamadas {name!r}; usando {chosen.get('id')}",
                file=sys.stderr,
            )
        repo_id = chosen.get("id") or chosen.get("repo_id")
        if not repo_id:
            raise SeafileError(f"biblioteca {name!r} sem id")
        return repo_id
    created = session.post(
        f"{base}/api2/repos/",
        data={"name": name, "desc": "Cópia do Google Drive, sem a pasta backup ssd."},
        timeout=(30, 120),
    )
    created.raise_for_status()
    payload = created.json()
    repo_id = payload.get("repo_id") or payload.get("id")
    if not repo_id:
        raise SeafileError(f"não criei a biblioteca: {payload!r}")
    print(f"biblioteca criada: {name}", file=sys.stderr)
    return repo_id


def drive_credentials(credentials_path: Path, token_path: Path):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    scopes = ["https://www.googleapis.com/auth/drive.readonly"]
    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), scopes)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not credentials_path.exists():
                raise SystemExit(
                    f"falta {credentials_path}. Baixe o cliente OAuth (app para computador) "
                    "e salve o JSON nesse caminho. O --help explica o passo a passo."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), scopes)
            creds = flow.run_local_server(port=0)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json(), encoding="utf-8")
        token_path.chmod(0o600)
    return creds


def list_drive_items(service) -> tuple[dict[str, DriveItem], str]:
    from googleapiclient.errors import HttpError

    root = service.files().get(fileId="root", fields="id").execute()
    items: dict[str, DriveItem] = {}
    token = None
    incomplete = False
    while True:
        response = (
            service.files()
            .list(
                q="trashed = false",
                pageSize=1000,
                pageToken=token,
                fields=f"nextPageToken, incompleteSearch, files({DRIVE_FIELDS})",
                spaces="drive",
                corpora="user",
            )
            .execute()
        )
        for raw in response.get("files", []):
            item = item_from_api(raw)
            items[item.id] = item
        if response.get("incompleteSearch"):
            incomplete = True
        token = response.get("nextPageToken")
        print(f"listando Drive: {len(items)} itens", file=sys.stderr)
        if not token:
            break
    if incomplete:
        print(
            "aviso: a busca do Drive veio incompleta; pode faltar arquivo",
            file=sys.stderr,
        )

    missing: list[str] = []
    for item in list(items.values()):
        if item.is_shortcut and item.target_id and item.target_id not in items:
            missing.append(item.target_id)
    for target_id in dict.fromkeys(missing):
        try:
            raw = service.files().get(fileId=target_id, fields=DRIVE_FIELDS).execute()
        except HttpError as exc:
            print(f"aviso: não li o destino do atalho {target_id}: {exc}", file=sys.stderr)
            continue
        fetched = item_from_api(raw, listed=False)
        items[fetched.id] = fetched
    return items, root["id"]


def download_to(service, action: Upload, dest: Path) -> None:
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaIoBaseDownload

    def request():
        if action.export_mime:
            return service.files().export_media(fileId=action.drive_id, mimeType=action.export_mime)
        return service.files().get_media(fileId=action.drive_id)

    delay = 2.0
    for attempt in range(4):
        try:
            with dest.open("wb") as handle:
                downloader = MediaIoBaseDownload(handle, request(), chunksize=8 * 1024 * 1024)
                done = False
                while not done:
                    _, done = downloader.next_chunk()
            return
        except HttpError as exc:
            text = str(exc)
            if "exportSizeLimitExceeded" in text or "exportSizeLimit" in text:
                raise SkipFile("o Drive não exporta este arquivo (limite de 10 MB)") from exc
            status = getattr(getattr(exc, "resp", None), "status", None)
            limited = status == 403 and ("Rate" in text or "rateLimit" in text)
            if (status in {429, 500, 502, 503} or limited) and attempt < 3:
                print(f"aviso: Drive respondeu {status} em {action.path}; nova tentativa", file=sys.stderr)
                time.sleep(delay)
                delay *= 2
                continue
            raise


def format_bytes(size: int | None) -> str:
    if size is None:
        return "?"
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def print_plan(plan: Plan, *, verbose: bool, limit: int | None) -> None:
    shown = plan.uploads if limit is None else plan.uploads[:limit]
    for action in shown:
        print(f"enviar  {action.path}  ({format_bytes(action.size)})")
    hidden = len(plan.uploads) - len(shown)
    if hidden > 0:
        print(f"... e mais {hidden} arquivos (corte do --limit)")
    for skip in plan.skips:
        if verbose or skip.reason != "já sincronizado":
            print(f"ignorar {skip.path}  ({skip.reason})")
    synced = sum(1 for skip in plan.skips if skip.reason == "já sincronizado")
    excluded = sum(1 for skip in plan.skips if skip.reason == "pasta excluída")
    print(
        f"{len(shown)} a enviar, {excluded} pastas excluídas, "
        f"{synced} já sincronizados, {plan.orphans} fora do Meu Drive"
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Copia o Meu Drive para o Seafile, sem a pasta 'backup ssd'.",
        epilog=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dry-run", action="store_true", help="Só lista o que seria enviado.")
    parser.add_argument("--force", action="store_true", help="Reenvia mesmo o que o estado local já marcou.")
    parser.add_argument("--verbose", action="store_true", help="Mostra também o que já estava sincronizado.")
    parser.add_argument("--limit", type=int, default=None, help="Envia no máximo N arquivos.")
    parser.add_argument(
        "--exclude",
        action="append",
        default=None,
        help="Nome da pasta a ignorar, com tudo dentro. Pode repetir. Padrão: 'backup ssd'.",
    )
    parser.add_argument("--url", default=os.environ.get("SEAFILE_URL", DEFAULT_SERVER))
    parser.add_argument("--user", default=os.environ.get("SEAFILE_USER", DEFAULT_USER))
    parser.add_argument("--repo", default=os.environ.get("SEAFILE_REPO", DEFAULT_REPO))
    parser.add_argument("--credentials", type=Path, default=SCRIPT_DIR / "credentials.json")
    parser.add_argument("--token", type=Path, default=SCRIPT_DIR / "token.json")
    parser.add_argument("--state", type=Path, default=SCRIPT_DIR / ".drive-sync" / "state.json")
    return parser.parse_args(argv)


def _password(args: argparse.Namespace) -> str:
    token = os.environ.get("SEAFILE_TOKEN")
    if token:
        return ""
    password = os.environ.get("SEAFILE_PASSWORD", "")
    if password:
        return password
    if not sys.stdin.isatty():
        raise SystemExit("Defina SEAFILE_PASSWORD ou SEAFILE_TOKEN.")
    typed = getpass.getpass("Senha do Seafile: ")
    if not typed:
        raise SystemExit("Senha vazia.")
    return typed


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    exclude = args.exclude if args.exclude is not None else [DEFAULT_EXCLUDE]
    server = args.url.rstrip("/")
    parsed = urllib.parse.urlparse(server)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise SystemExit(f"SEAFILE_URL inválida: {server}")

    creds = drive_credentials(args.credentials, args.token)
    from googleapiclient.discovery import build

    service = build("drive", "v3", credentials=creds, cache_discovery=False)
    items, root_id = list_drive_items(service)
    state = load_state(args.state, server, args.repo)
    synced = {} if args.force else state["files"]
    plan = build_plan(items, root_id, exclude, synced)

    if args.dry_run:
        print_plan(plan, verbose=args.verbose, limit=args.limit)
        return 0

    token = os.environ.get("SEAFILE_TOKEN") or seafile_token(server, args.user, _password(args))
    repo_id = ensure_repo(server, token, args.repo)
    client = SeafileClient(server, token, repo_id)
    capped = plan.uploads if args.limit is None else plan.uploads[: args.limit]
    if args.limit is None:
        directories = plan.mkdirs
    else:
        directories = []
        seen_dirs: set[str] = set()
        for action in capped:
            if action.parent_dir in seen_dirs or action.parent_dir == "/":
                continue
            seen_dirs.add(action.parent_dir)
            directories.append(action.parent_dir)
    print(
        f"{len(capped)} arquivos para enviar, {len(directories)} pastas, biblioteca {args.repo!r}",
        file=sys.stderr,
    )
    for directory in directories:
        client.ensure_dir(directory)

    def persist() -> None:
        save_state(args.state, state)

    def _download(action: Upload, dest: Path) -> None:
        download_to(service, action, dest)

    def _upload(action: Upload, dest: Path) -> None:
        with dest.open("rb") as handle:
            client.upload_file(action.parent_dir, action.filename, handle)

    try:
        counts = execute(
            plan,
            state,
            _download,
            _upload,
            workdir=args.state.parent,
            limit=args.limit,
            on_saved=persist,
        )
    except QuotaExceeded as exc:
        print(f"sem espaço no Seafile: {exc}", file=sys.stderr)
        persist()
        return 1

    persist()
    print(
        f"enviados {counts['enviados']}, erros {counts['erros']}, "
        f"pulados {counts['pulados']}. Estado em {args.state}",
        file=sys.stderr,
    )
    return 1 if counts["erros"] else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("interrompido; o que já foi enviado fica no estado", file=sys.stderr)
        raise SystemExit(130)
