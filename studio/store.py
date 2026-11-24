"""SQLite persistence for the local Studio API."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .source_evidence import validate_source_enrichment, valid_source_url
from .editorial import (
    EditorialError, approval_fingerprint, default_reader_brief, deterministic_review,
    normalize_editorial, normalize_model_review_semantics, suggest_angles, validate_claim_map, validate_reader_brief,
)

X_ID_RE = re.compile(r"^\d{15,20}$")
X_URL_RE = re.compile(r"^https://(?:www\.)?x\.com/[A-Za-z0-9_]{1,15}/status/(\d{15,20})(?:[/?#].*)?$")
MANUAL_SOURCE_KINDS = {"x", "web", "instagram", "youtube_transcript"}


class StoreError(Exception):
    """A domain error which may be rendered safely to an API client."""


class RevisionConflict(StoreError):
    pass


class StudioStore:
    def __init__(self, db_path: Path | str, *, model_mission: str = 'legacy', read_only: bool = False) -> None:
        if model_mission not in {'legacy', 'extended-modernization-2026-09-08', 'organic-growth-v1'}:
            raise StoreError('Bilinmeyen model bütçesi; yeni sayaç açılmadı.')
        self.model_mission = model_mission
        self.db_path = str(db_path)
        self.read_only = bool(read_only)
        if self.read_only:
            if not Path(self.db_path).is_file():
                raise StoreError("Salt-okunur inceleme için mevcut Studio veritabanı bulunamadı.")
        else:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            self._initialize()

    def _connect(self) -> sqlite3.Connection:
        if self.read_only:
            uri = Path(self.db_path).resolve().as_uri() + "?mode=ro"
            connection = sqlite3.connect(uri, isolation_level=None, uri=True)
            connection.execute("PRAGMA query_only=ON")
        else:
            connection = sqlite3.connect(self.db_path, isolation_level=None)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def final_package_lock(self):
        """Reserve SQLite's writer slot from final gate validation through ZIP verification.

        All ordinary Studio mutations use separate SQLite connections. Starting the
        write transaction *before* any draft/proposal read makes those mutations wait
        until this final package has been fully persisted or rejected.
        """
        if self.read_only:
            raise StoreError("Salt-okunur Studio son paket kaydedemez.")
        with closing(self._connect()) as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as exc:
                if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                    raise RevisionConflict("Studio meşgul; son yerel kaydı yeniden deneyin.") from exc
                raise
            try:
                yield
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            else:
                conn.execute("COMMIT")

    def _initialize(self) -> None:
        with closing(self._connect()) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS bookmarks (
                    id TEXT PRIMARY KEY, url TEXT NOT NULL, title TEXT NOT NULL,
                    text TEXT NOT NULL, author TEXT NOT NULL, observed_at TEXT NOT NULL,
                    evidence_note TEXT NOT NULL, verified_urls TEXT NOT NULL,
                    source TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'x',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS source_evidence (
                    source_id TEXT PRIMARY KEY REFERENCES bookmarks(id),
                    source_url TEXT NOT NULL,
                    data TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS imports (
                    id INTEGER PRIMARY KEY CHECK (id = 1), complete INTEGER NOT NULL DEFAULT 0
                );
                INSERT OR IGNORE INTO imports(id, complete) VALUES(1, 0);
                CREATE TABLE IF NOT EXISTS drafts (
                    id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES bookmarks(id),
                    revision INTEGER NOT NULL, data TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS draft_history (
                    draft_id TEXT NOT NULL REFERENCES drafts(id), revision INTEGER NOT NULL,
                    data TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(draft_id, revision)
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, draft_id TEXT NOT NULL,
                    revision INTEGER NOT NULL, status TEXT NOT NULL, detail TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS model_runs (
                    job_id TEXT PRIMARY KEY REFERENCES jobs(id), kind TEXT NOT NULL,
                    started_at TEXT, completed_at TEXT, duration_ms INTEGER,
                    response_id TEXT, output_sha256 TEXT, success INTEGER
                );
                CREATE TABLE IF NOT EXISTS proposals (
                    id TEXT PRIMARY KEY, draft_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    data TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending'
                );
                CREATE TABLE IF NOT EXISTS model_run_scopes (
                    job_id TEXT PRIMARY KEY, mission TEXT NOT NULL
                );
                """
            )
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(bookmarks)")}
            if "kind" not in columns:
                conn.execute("ALTER TABLE bookmarks ADD COLUMN kind TEXT NOT NULL DEFAULT 'x'")
            # Older databases have no trustworthy intermediate revisions. Seed
            # only the state that actually exists now; do not invent a past.
            conn.execute(
                """INSERT OR IGNORE INTO draft_history(draft_id, revision, data, created_at)
                SELECT id, revision, data, created_at FROM drafts"""
            )
            # A process restart cannot resume an in-memory job safely. Do not
            # leave it looking runnable forever.
            conn.execute(
                "UPDATE jobs SET status = 'failed', detail = 'failedInterrupted', updated_at = ? "
                "WHERE status IN ('pending', 'running')",
                (datetime.now(timezone.utc).isoformat(),),
            )

    @staticmethod
    def validate_source(item: Any) -> dict[str, Any]:
        if not isinstance(item, dict):
            raise StoreError("Kaynak öğesi nesne olmalıdır.")
        required = ("id", "url", "title", "text", "author", "observed_at", "evidence_note", "verified_urls")
        if any(key not in item for key in required):
            raise StoreError("Kaynak alanları eksik.")
        item_id, url = item["id"], item["url"]
        if not isinstance(item_id, str) or not X_ID_RE.fullmatch(item_id):
            raise StoreError("X kaynak kimliği 15-20 haneli olmalıdır.")
        match = X_URL_RE.fullmatch(url) if isinstance(url, str) else None
        if not match or match.group(1) != item_id or not valid_source_url(url):
            raise StoreError("Kaynak URL'si X durum kimliğiyle eşleşmelidir.")
        if not isinstance(item["verified_urls"], list) or not all(valid_source_url(value) for value in item["verified_urls"]):
            raise StoreError("Doğrulanmış URL listesi geçersiz.")
        result = {key: item[key] for key in required}
        result["kind"] = "x"
        for key in ("url", "title", "text", "author", "observed_at", "evidence_note"):
            if not isinstance(result[key], str) or len(result[key]) > 20_000:
                raise StoreError("Kaynak metin alanı geçersiz veya fazla uzun.")
        return result

    def import_sources(self, items: list[Any], source: str, complete: bool) -> dict[str, Any]:
        if source not in {"browser_bookmarks", "manual_bookmark"}:
            raise StoreError("Kaynak türü desteklenmiyor.")
        if not isinstance(items, list) or len(items) > 500:
            raise StoreError("İçe aktarma öğe sayısı geçersiz.")
        valid = [self.validate_source(item) for item in items]
        imported = duplicates = 0
        with closing(self._connect()) as conn:
            for item in valid:
                found = conn.execute("SELECT 1 FROM bookmarks WHERE id = ?", (item["id"],)).fetchone()
                if found:
                    duplicates += 1
                    continue
                conn.execute(
                    """INSERT INTO bookmarks(id,url,title,text,author,observed_at,evidence_note,verified_urls,source,kind)
                    VALUES(:id,:url,:title,:text,:author,:observed_at,:evidence_note,:verified_urls,:source,:kind)""",
                    {**item, "verified_urls": json.dumps(item["verified_urls"], ensure_ascii=False), "source": source},
                )
                imported += 1
            conn.execute("UPDATE imports SET complete = ? WHERE id = 1", (int(bool(complete)),))
            total = conn.execute("SELECT COUNT(*) FROM bookmarks").fetchone()[0]
        return {"imported": imported, "duplicates": duplicates, "total": total, "complete": bool(complete)}

    def create_manual_source(self, payload: Any) -> dict[str, Any]:
        """Store user-carried material without fetching its URL or executing its text."""
        required = {"kind", "url", "title", "text", "author"}
        if not isinstance(payload, dict) or set(payload) != required:
            raise StoreError("Elle kaynak alanları eksik veya fazla.")
        if payload["kind"] not in MANUAL_SOURCE_KINDS:
            raise StoreError("Kaynak türü desteklenmiyor.")
        if not all(isinstance(payload[key], str) for key in required):
            raise StoreError("Elle kaynak alanları metin olmalıdır.")
        kind = payload["kind"]
        url, title, text, author = (payload[key].strip() for key in ("url", "title", "text", "author"))
        if not url or len(url) > 2_000 or not title or len(title) > 500 or not text or len(text) > 20_000 or len(author) > 500:
            raise StoreError("Kaynak URL, başlık veya taşınan metin geçersiz.")
        if not valid_source_url(url):
            raise StoreError("Kaynak URL kullanıcı bilgisi, sorgu veya parça içeremez; geçerli HTTPS gerekir.")
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not host or any(ord(char) < 32 for char in url):
            raise StoreError("Kaynak URL'si geçerli bir HTTPS adresi olmalıdır.")
        if kind == "x":
            match = X_URL_RE.fullmatch(url)
            if not match:
                raise StoreError("X kaynağı geçerli bir durum bağlantısı olmalıdır.")
            source_id = match.group(1)
        else:
            if kind == "instagram" and host not in {"instagram.com", "www.instagram.com"}:
                raise StoreError("Instagram kaynağı instagram.com adresinde olmalıdır.")
            if kind == "youtube_transcript" and host not in {"youtube.com", "www.youtube.com", "youtu.be"}:
                raise StoreError("YouTube transkripti youtube.com veya youtu.be adresinde olmalıdır.")
            identity = json.dumps({"kind": kind, "url": url, "title": title, "text": text, "author": author}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            source_id = "src_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
        observed_at = datetime.now(timezone.utc).isoformat()
        source_name = f"manual_{kind}"
        with closing(self._connect()) as conn:
            existing = conn.execute("SELECT * FROM bookmarks WHERE id=?", (source_id,)).fetchone()
            if existing is None:
                conn.execute(
                    """INSERT INTO bookmarks(id,url,title,text,author,observed_at,evidence_note,verified_urls,source,kind)
                    VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (source_id, url, title, text, author, observed_at, "Kullanıcı tarafından elle taşındı; URL otomatik okunmadı.", "[]", source_name, kind),
                )
            elif existing["url"] != url or existing["text"] != text or existing["kind"] != kind:
                raise StoreError("Aynı kaynak kimliği farklı içerikle yeniden kullanılamaz.")
        return self._source(source_id)

    def delete_manual_source(self, source_id: str, expected_url: str) -> dict[str, Any]:
        """Delete only an unused user-carried source; preserve every draft snapshot."""
        if self.read_only:
            raise StoreError("Salt-okunur Studio kaynak silemez.")
        if not isinstance(source_id, str) or not source_id or not isinstance(expected_url, str) or not expected_url:
            raise StoreError("Kaynak silmek için kimlik ve görülen bağlantı zorunludur.")
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                source = conn.execute("SELECT url, source FROM bookmarks WHERE id=?", (source_id,)).fetchone()
                if source is None:
                    raise StoreError("Silinecek kaynak bulunamadı.")
                if source["url"] != expected_url:
                    raise StoreError("Kaynak bağlantısı değişti; listeyi yenileyip tekrar incele.")
                if not source["source"].startswith("manual_"):
                    raise StoreError("Aktarılmış kaynaklar bu ekrandan silinemez.")
                if conn.execute("SELECT 1 FROM drafts WHERE source_id=? LIMIT 1", (source_id,)).fetchone():
                    raise StoreError("Bu kaynak bir çalışmada kullanılıyor; silinemez.")
                # Also protect attached sources, including a historical version that
                # could later be restored. Never orphan a project's frozen evidence.
                for row in conn.execute("SELECT data FROM draft_history"):
                    draft = json.loads(row["data"])
                    if draft.get("source_id") == source_id or any(
                        isinstance(item, dict) and item.get("id") == source_id
                        for item in draft.get("project_sources") or []
                    ):
                        raise StoreError("Bu kaynak bir çalışmanın geçmişinde kullanılıyor; silinemez.")
                conn.execute("DELETE FROM source_evidence WHERE source_id=?", (source_id,))
                deleted = conn.execute("DELETE FROM bookmarks WHERE id=?", (source_id,))
                if deleted.rowcount != 1:
                    raise StoreError("Kaynak değişti; listeyi yenileyip tekrar incele.")
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        return {"deleted": True, "id": source_id}

    def apply_source_enrichment(self, payload: Any) -> dict[str, int]:
        """Atomically persist checked evidence without changing the bookmarked source."""
        try:
            items = validate_source_enrichment(payload)
        except ValueError as exc:
            raise StoreError(str(exc)) from exc
        inserted = updated = unchanged = 0
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                # Verify every identity before writing any evidence so an unknown
                # source or URL drift rejects the whole batch.
                for item in items:
                    source = conn.execute("SELECT url FROM bookmarks WHERE id = ?", (item["id"],)).fetchone()
                    if source is None:
                        raise StoreError("Kaynak kanıtı bilinmeyen bir kaynağa ait.")
                    if source["url"] != item["url"]:
                        raise StoreError("Kaynak kanıtı URL'si kaydedilen kaynakla eşleşmiyor.")
                for item in items:
                    encoded = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
                    existing = conn.execute("SELECT data FROM source_evidence WHERE source_id = ?", (item["id"],)).fetchone()
                    if existing is None:
                        conn.execute(
                            "INSERT INTO source_evidence(source_id,source_url,data,updated_at) VALUES(?,?,?,?)",
                            (item["id"], item["url"], encoded, datetime.now(timezone.utc).isoformat()),
                        )
                        inserted += 1
                    elif existing["data"] == encoded:
                        unchanged += 1
                    else:
                        raise StoreError("Mevcut kaynak kanıtı değiştirilemez; yeni sürüm açıkça eklenmelidir.")
                total = conn.execute("SELECT COUNT(*) FROM source_evidence").fetchone()[0]
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return {"inserted": inserted, "updated": updated, "unchanged": unchanged, "total": total}

    @staticmethod
    def _decode_source(row: sqlite3.Row) -> dict[str, Any]:
        source = dict(row)
        encoded_evidence = source.pop("source_evidence_data", None)
        source["verified_urls"] = json.loads(source["verified_urls"])
        source["source_evidence"] = json.loads(encoded_evidence) if encoded_evidence is not None else None
        source["kind"] = source.get("kind") or "x"
        return source

    def list_bookmarks(self) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """SELECT bookmarks.*, source_evidence.data AS source_evidence_data
                FROM bookmarks LEFT JOIN source_evidence ON source_evidence.source_id = bookmarks.id
                ORDER BY bookmarks.created_at, bookmarks.id"""
            ).fetchall()
        return [self._decode_source(row) for row in rows]

    def import_complete(self) -> bool:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT complete FROM imports WHERE id = 1").fetchone()
        return bool(row and row["complete"])

    def _source(self, source_id: str) -> dict[str, Any]:
        with closing(self._connect()) as conn:
            row = conn.execute(
                """SELECT bookmarks.*, source_evidence.data AS source_evidence_data
                FROM bookmarks LEFT JOIN source_evidence ON source_evidence.source_id = bookmarks.id
                WHERE bookmarks.id = ?""",
                (source_id,),
            ).fetchone()
        if row is None:
            raise StoreError("Kaynak bulunamadı.")
        return self._decode_source(row)

    @staticmethod
    def _decode_draft(row: sqlite3.Row) -> dict[str, Any]:
        data = json.loads(row["data"])
        data["revision"] = row["revision"]
        if not isinstance(data.get("project_sources"), list) or not data["project_sources"]:
            snapshot = data.get("source_snapshot")
            if isinstance(snapshot, dict):
                primary = json.loads(json.dumps(snapshot))
                primary.setdefault("kind", "x")
                data["project_sources"] = [primary]
            else:
                data["project_sources"] = [{
                    "id": data.get("source_id", ""), "url": data.get("source_url", ""),
                    "title": "", "text": "", "author": "", "kind": "x",
                    "observed_at": data.get("observed_at", ""),
                    "evidence_note": data.get("evidence_note", ""), "verified_urls": [],
                    "source": "legacy_partial",
                }]
        for asset in data.get("assets", []):
            asset["stale"] = asset.get("revision") != row["revision"]
        editorial = normalize_editorial(data)
        editorial["reviews"]["model"] = [
            normalize_model_review_semantics(review)
            for review in editorial["reviews"]["model"]
        ]
        data["editorial"] = editorial
        data.update({key: editorial[key] for key in ("reader_brief", "claim_map", "rights_note", "reviews", "audit", "approvals")})
        data["approval_fingerprint"] = approval_fingerprint(data)
        return data

    @staticmethod
    def _store_editorial(draft: dict[str, Any]) -> None:
        editorial = normalize_editorial(draft)
        draft["editorial"] = editorial
        draft.update({key: editorial[key] for key in ("reader_brief", "claim_map", "rights_note", "reviews", "audit", "approvals")})
        draft["approval_fingerprint"] = approval_fingerprint(draft)

    @staticmethod
    def _clear_approvals(draft: dict[str, Any]) -> None:
        editorial = normalize_editorial(draft)
        editorial["approvals"] = {}
        draft["editorial"] = editorial
        draft["copy_approved"] = False
        draft["publication_ready"] = False
        draft["x_render_verified"] = False
        StudioStore._store_editorial(draft)

    def create_draft(self, source_id: str, mode: str, objective: str, audience: str) -> dict[str, Any]:
        if mode not in {"research", "explainer", "experiment"}:
            raise StoreError("Taslak modu desteklenmiyor.")
        if not all(isinstance(value, str) and len(value) <= 5_000 for value in (objective, audience)):
            raise StoreError("Taslak amacı veya kitlesi geçersiz.")
        source = self._source(source_id)
        draft_id = uuid.uuid4().hex
        source_snapshot = {key: source.get(key) for key in ('id','url','title','text','author','observed_at','evidence_note','verified_urls','source','kind')}
        draft = {
            "id": draft_id, "source_id": source_id, "source_url": source["url"], "revision": 1, "title": "", "mode": mode,
            "main": "", "reply": "", "objective": objective, "audience": audience, "scene_texts": [],
            "cloud_tts_allowed": False, "voice": "tr-TR-AhmetNeural", "copy_approved": False,
            "publication_ready": False, "x_render_verified": False, "assets": [], "primary_media": None, "plan_date": "",
            "feedback": "", "observed_at": source["observed_at"], "evidence_note": source["evidence_note"], "source_evidence": source["source_evidence"],
            "source_snapshot": source_snapshot, "project_sources": [source_snapshot],
        }
        draft["editorial"] = {"reader_brief": default_reader_brief(draft), "claim_map": None, "rights_note": "", "reviews": {"deterministic": [], "model": []}, "audit": None, "approvals": {}}
        self._store_editorial(draft)
        encoded = json.dumps(draft, ensure_ascii=False)
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute("INSERT INTO drafts(id,source_id,revision,data) VALUES(?,?,?,?)", (draft_id, source_id, 1, encoded))
                conn.execute("INSERT INTO draft_history(draft_id,revision,data) VALUES(?,?,?)", (draft_id, 1, encoded))
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        draft["revision"] = 1
        return draft

    def attach_project_source(self, draft_id: str, revision: int, source_id: str) -> dict[str, Any]:
        """Explicitly attach a frozen source snapshot to one content project."""
        if type(revision) is not int or not isinstance(source_id, str) or not source_id:
            raise StoreError("Proje kaynağı için geçerli revizyon ve kaynak kimliği zorunludur.")
        source = self._source(source_id)
        snapshot = {key: source.get(key) for key in ('id','url','title','text','author','observed_at','evidence_note','verified_urls','source','kind')}
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
            if row is None:
                raise StoreError("İçerik projesi bulunamadı.")
            if row["revision"] != revision:
                raise RevisionConflict("İçerik projesi başka bir düzenleme ile değişti.")
            draft = self._decode_draft(row)
            if any(item.get("id") == source_id for item in draft["project_sources"] if isinstance(item, dict)):
                return draft
            draft["project_sources"] = [*draft["project_sources"], snapshot]
            editorial = normalize_editorial(draft)
            editorial["audit"] = {
                "status": "stale", "revision": revision, "at": datetime.now(timezone.utc).isoformat(),
                "reason": "İçerik projesine yeni kaynak eklendi; inceleme ve insan onayı yeniden yapılmalıdır.",
            }
            draft["editorial"] = editorial
            self._clear_approvals(draft)
            next_revision = revision + 1
            draft["revision"] = next_revision
            self._store_editorial(draft)
            encoded = json.dumps(draft, ensure_ascii=False)
            conn.execute("BEGIN IMMEDIATE")
            try:
                changed = conn.execute(
                    "UPDATE drafts SET revision=?,data=? WHERE id=? AND revision=? AND data=?",
                    (next_revision, encoded, draft_id, revision, row["data"]),
                )
                if changed.rowcount != 1:
                    raise RevisionConflict("İçerik projesi eşzamanlı başka bir düzenleme ile değişti.")
                conn.execute("INSERT INTO draft_history(draft_id,revision,data) VALUES(?,?,?)", (draft_id, next_revision, encoded))
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return self.get_draft(draft_id)

    def get_draft(self, draft_id: str) -> dict[str, Any]:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
        if row is None:
            raise StoreError("Taslak bulunamadı.")
        return self._decode_draft(row)

    def list_drafts(self) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT * FROM drafts ORDER BY created_at, id").fetchall()
        return [self._decode_draft(row) for row in rows]

    def list_history(self, draft_id: str, limit: int = 20) -> list[dict[str, Any]]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 50:
            raise StoreError("Tarihçe sınırı 1–50 arasında olmalıdır.")
        with closing(self._connect()) as conn:
            if conn.execute("SELECT 1 FROM drafts WHERE id = ?", (draft_id,)).fetchone() is None:
                raise StoreError("Taslak bulunamadı.")
            rows = conn.execute(
                "SELECT revision, data, created_at FROM draft_history WHERE draft_id = ? ORDER BY revision DESC LIMIT ?",
                (draft_id, limit),
            ).fetchall()
        return [{**json.loads(row["data"]), "revision": row["revision"], "saved_at": row["created_at"]} for row in rows]

    def update_draft(self, draft_id: str, revision: int, changes: dict[str, Any], *, _proposal_id: str | None = None) -> dict[str, Any]:
        editable = {"title", "main", "reply", "objective", "audience", "scene_texts", "cloud_tts_allowed", "voice", "plan_date", "feedback", "mode", "alt_text", "reader_brief", "claim_map", "rights_note"}
        unknown = set(changes) - editable
        if unknown:
            raise StoreError("Taslak alanı düzenlenemez.")
        if not isinstance(revision, int):
            raise StoreError("Taslak revizyonu geçersiz.")
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
            if row is None:
                raise StoreError("Taslak bulunamadı.")
            if row["revision"] != revision:
                raise RevisionConflict("Taslak başka bir düzenleme ile değişti.")
            draft = self._decode_draft(row)
            if changes.get('mode', draft['mode']) not in {'research', 'explainer', 'experiment'}:
                raise StoreError('Taslak modu desteklenmiyor.')
            if len(str(changes.get('alt_text', ''))) > 1000:
                raise StoreError('Alt metin en fazla 1000 karakter olabilir.')
            source = self.source_for_draft(draft_id)
            if "reader_brief" in changes:
                try:
                    changes["reader_brief"] = validate_reader_brief(changes["reader_brief"], allowed_angles=suggest_angles(draft, source, changes["reader_brief"]), progressive=True)
                except EditorialError as exc:
                    raise StoreError(str(exc)) from exc
            brief = changes.get("reader_brief", draft["reader_brief"])
            if "claim_map" in changes:
                try:
                    changes["claim_map"] = validate_claim_map(changes["claim_map"], brief, draft.get("assets", []), allowed_urls=self.allowed_source_urls(self.source_for_draft(draft_id)))
                except EditorialError as exc:
                    raise StoreError(str(exc)) from exc
            if "rights_note" in changes and (not isinstance(changes["rights_note"], str) or len(changes["rights_note"]) > 2_000):
                raise StoreError("Hak notu geçersiz.")
            if _proposal_id is None and all(draft.get(key, '') == value for key, value in changes.items()):
                return draft
            text_fields = {"title", "main", "reply", "scene_texts"}
            text_changed_without_fresh_map = bool(text_fields.intersection(changes)) and "claim_map" not in changes
            for key, value in changes.items():
                if key == "scene_texts":
                    if not isinstance(value, list) or not all(isinstance(text, str) and len(text) <= 600 for text in value):
                        raise StoreError("Sahne metinleri geçersiz.")
                elif key == "cloud_tts_allowed":
                    if not isinstance(value, bool):
                        raise StoreError("Ses izin alanı geçersiz.")
                elif key in {"reader_brief", "claim_map"}:
                    pass
                elif not isinstance(value, str) or len(value) > 20_000:
                    raise StoreError("Taslak metin alanı geçersiz.")
                draft[key] = value
            if text_changed_without_fresh_map:
                draft["claim_map"] = None
            if changes:
                draft["primary_media"] = None
                editorial = normalize_editorial(draft)
                editorial["reader_brief"] = draft.get("reader_brief", editorial["reader_brief"])
                editorial["claim_map"] = draft.get("claim_map", editorial["claim_map"])
                editorial["rights_note"] = draft.get("rights_note", editorial["rights_note"])
                stale_fields = {"title", "main", "reply", "scene_texts", "reader_brief", "claim_map", "rights_note"}
                if stale_fields.intersection(changes):
                    changed_labels = []
                    if {"title", "main", "reply", "scene_texts"}.intersection(changes):
                        changed_labels.append("metin")
                    if "claim_map" in changes:
                        changed_labels.append("claim map")
                    elif text_changed_without_fresh_map:
                        changed_labels.append("eski claim map kaldırıldı")
                    if {"reader_brief", "rights_note"}.intersection(changes):
                        changed_labels.append("editoryal bağlam")
                    editorial["audit"] = {"status": "stale", "revision": revision, "at": datetime.now(timezone.utc).isoformat(), "reason": ", ".join(changed_labels) + " değişti; inceleme ve insan onayı yeniden yapılmalıdır."}
                else:
                    editorial["audit"] = None
                draft["editorial"] = editorial
                self._clear_approvals(draft)
            next_revision = revision + 1
            draft["revision"] = next_revision
            self._store_editorial(draft)
            encoded = json.dumps(draft, ensure_ascii=False)
            conn.execute("BEGIN IMMEDIATE")
            try:
                if _proposal_id is not None:
                    consumed=conn.execute("UPDATE proposals SET status='applied' WHERE id=? AND draft_id=? AND revision=? AND status='pending'",(_proposal_id,draft_id,revision))
                    if consumed.rowcount != 1:
                        raise RevisionConflict('Öneriden vazgeçildi veya öneri uygulandı; metin korunuyor.')
                conn.execute("INSERT OR IGNORE INTO draft_history(draft_id,revision,data) VALUES(?,?,?)", (draft_id, revision, row['data']))
                changed = conn.execute("UPDATE drafts SET revision = ?, data = ? WHERE id = ? AND revision = ? AND data = ?", (next_revision, encoded, draft_id, revision, row['data']))
                if changed.rowcount != 1:
                    raise RevisionConflict('Taslak eşzamanlı başka bir düzenleme ile değişti.')
                conn.execute("INSERT INTO draft_history(draft_id,revision,data) VALUES(?,?,?)", (draft_id, next_revision, encoded))
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        draft["revision"] = next_revision
        for asset in draft["assets"]:
            asset["stale"] = asset.get("revision") != next_revision
        return draft

    def restore_draft(self, draft_id: str, revision: int, restore_revision: int) -> dict[str, Any]:
        if not all(isinstance(value, int) and not isinstance(value, bool) for value in (revision, restore_revision)):
            raise StoreError("Taslak revizyonları geçersiz.")
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
            if row is None:
                raise StoreError("Taslak bulunamadı.")
            if row["revision"] != revision:
                raise RevisionConflict("Taslak başka bir düzenleme ile değişti.")
            snapshot = conn.execute("SELECT data FROM draft_history WHERE draft_id = ? AND revision = ?", (draft_id, restore_revision)).fetchone()
            if snapshot is None:
                raise StoreError("Geri dönülecek revizyon bulunamadı.")
            restored = json.loads(snapshot["data"])
            # Keep actual generated/uploaded files discoverable; the new revision
            # makes every existing asset stale instead of silently dropping it.
            restored["assets"] = json.loads(row["data"]).get("assets", [])
            restored["primary_media"] = None
            self._clear_approvals(restored)
            next_revision = revision + 1
            restored["revision"] = next_revision
            self._store_editorial(restored)
            encoded = json.dumps(restored, ensure_ascii=False)
            conn.execute("BEGIN IMMEDIATE")
            try:
                changed = conn.execute(
                    "UPDATE drafts SET revision = ?, data = ? WHERE id = ? AND revision = ? AND data = ?",
                    (next_revision, encoded, draft_id, revision, row["data"]),
                )
                if changed.rowcount != 1:
                    raise RevisionConflict("Taslak eşzamanlı başka bir düzenleme ile değişti.")
                conn.execute("INSERT INTO draft_history(draft_id,revision,data) VALUES(?,?,?)", (draft_id, next_revision, encoded))
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        restored["revision"] = next_revision
        for asset in restored.get("assets", []):
            asset["stale"] = asset.get("revision") != next_revision
        return restored

    @staticmethod
    def _validate_text_approval(draft: dict[str, Any]) -> None:
        """Approve only X-valid copy; media/package checks remain separate."""
        main, reply = draft.get("main"), draft.get("reply")
        if not isinstance(main, str) or not main.strip():
            raise StoreError("X metin onayı için boş olmayan ana metin gereklidir.")
        if not isinstance(reply, str):
            raise StoreError("X metin onayı için yanıt metni geçersiz.")
        try:
            from .text_budget import measure_many
            counts = measure_many([main, reply])
        except ValueError as exc:
            raise StoreError("X metin sayacı doğrulanamadı; metin onayı kaydedilmedi.") from exc
        for label, text, count in zip(("Ana metin", "Yanıt"), (main, reply), counts):
            if not text:
                continue
            if not count.get("valid") or count.get("weighted_length", 281) > 280:
                raise StoreError(f"{label} X weighted 280 sınırını aşıyor veya geçersiz.")

    def review_draft(self, draft_id: str, revision: int) -> dict[str, Any]:
        raise StoreError("Eski /review onay kapısı kapalıdır; güncel parmak iziyle /approvals/copy ve confirmed:true kullanın.")

    def approve_copy(self, draft_id: str, revision: int, fingerprint: str) -> dict[str, Any]:
        if not isinstance(fingerprint, str) or len(fingerprint) != 64:
            raise StoreError("Metin onay parmak izi geçersiz.")
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
            if row is None:
                raise StoreError("Taslak bulunamadı.")
            if row["revision"] != revision:
                raise RevisionConflict("Taslak başka bir düzenleme ile değişti.")
            draft = self._decode_draft(row)
            self.require_editorial_ready(draft_id, revision)
            if approval_fingerprint(draft) != fingerprint:
                raise RevisionConflict("Onay parmak izi değişti; güncel taslağı inceleyin.")
            self._validate_text_approval(draft)
            if not deterministic_review(draft)['passed']:
                raise StoreError('Kural kontrolü geçmedi: başlık, ana metin, yanıt ve sahnelerin tüm iddia kapsamını güncelleyin.')
            draft["copy_approved"] = True
            editorial = normalize_editorial(draft)
            editorial["approvals"]["copy"] = {"fingerprint": fingerprint, "at": datetime.now(timezone.utc).isoformat(), "source": "explicit_human_confirmation"}
            draft["editorial"] = editorial
            self._store_editorial(draft)
            if conn.execute("UPDATE drafts SET data=? WHERE id=? AND revision=? AND data=?", (json.dumps(draft, ensure_ascii=False), draft_id, revision, row["data"])).rowcount != 1:
                raise RevisionConflict("Metin onayı kaydedilirken taslak değişti.")
        return draft

    def select_primary_media(self, draft_id: str, revision: int, filename: str) -> dict[str, Any]:
        if not isinstance(revision, int) or not isinstance(filename, str):
            raise StoreError("Birincil medya isteği geçersiz.")
        if filename and not self._safe_name(filename):
            raise StoreError("Varlık adı güvenli değil.")
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
                if row is None:
                    raise StoreError("Taslak bulunamadı.")
                if row["revision"] != revision:
                    raise RevisionConflict("Taslak başka bir düzenleme ile değişti.")
                draft = self._decode_draft(row)
                selected = None
                if filename:
                    selected = next((asset for asset in draft.get("assets", []) if asset.get("name") == filename and asset.get("revision") == revision and asset.get("kind") in {"image", "photo", "video"}), None)
                    if selected is None:
                        raise StoreError("Birincil medya yalnız geçerli taslağın görseli veya videosu olabilir.")
                primary = None if selected is None else {"name": selected["name"], "revision": revision, "sha256": selected["sha256"]}
                if draft.get("primary_media") == primary:
                    conn.execute("COMMIT")
                    return draft
                draft["primary_media"] = primary
                self._clear_approvals(draft)
                changed = conn.execute(
                    "UPDATE drafts SET data = ? WHERE id = ? AND revision = ? AND data = ?",
                    (json.dumps(draft, ensure_ascii=False), draft_id, revision, row["data"]),
                )
                if changed.rowcount != 1:
                    raise RevisionConflict("Birincil medya kaydedilirken taslak değişti.")
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return draft

    def create_job(self, kind: str, draft_id: str, revision: int) -> dict[str, Any]:
        if kind not in {"generate", "media", "transcribe", "model_review"}:
            raise StoreError("İş türü desteklenmiyor.")
        draft = self.get_draft(draft_id)
        if draft["revision"] != revision:
            raise RevisionConflict("Taslak başka bir düzenleme ile değişti.")
        if kind == "generate":
            self.require_generation_loop_clear(draft_id, revision)
        now = datetime.now(timezone.utc).isoformat()
        job = {"id": uuid.uuid4().hex, "kind": kind, "draft_id": draft_id, "revision": revision,
               "status": "pending", "detail": "", "created_at": now, "updated_at": now}
        with closing(self._connect()) as conn:
            conn.execute("INSERT INTO jobs(id,kind,draft_id,revision,status,detail,created_at,updated_at) VALUES(:id,:kind,:draft_id,:revision,:status,:detail,:created_at,:updated_at)", job)
        return job

    def start_job(self, job_id: str) -> dict[str, Any]:
        """Atomically claim a pending job only while its draft revision matches."""
        with closing(self._connect()) as conn:
            job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if job is None:
                raise StoreError("İş bulunamadı.")
            if job["status"] != "pending":
                raise StoreError("İş çalıştırılabilir durumda değil.")
            draft = conn.execute("SELECT revision FROM drafts WHERE id = ?", (job["draft_id"],)).fetchone()
            if draft is None or draft["revision"] != job["revision"]:
                conn.execute(
                    "UPDATE jobs SET status = 'failed', detail = ?, updated_at = ? WHERE id = ?",
                    ("Taslak başka bir düzenleme ile değişti.", datetime.now(timezone.utc).isoformat(), job_id),
                )
                raise RevisionConflict("Taslak başka bir düzenleme ile değişti.")
            conn.execute(
                "UPDATE jobs SET status = 'running', updated_at = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), job_id),
            )
        return dict(job)

    def list_jobs(self) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT * FROM jobs ORDER BY created_at, id").fetchall()
        return [dict(row) for row in rows]

    def set_job(self, job_id: str, status: str, detail: str = "") -> None:
        if status not in {"pending", "running", "succeeded", "failed"}:
            raise StoreError("İş durumu geçersiz.")
        with closing(self._connect()) as conn:
            conn.execute("UPDATE jobs SET status = ?, detail = ?, updated_at = ? WHERE id = ?", (status, detail[:1_000], datetime.now(timezone.utc).isoformat(), job_id))

    def start_model_run(self, job_id: str, kind: str) -> None:
        with closing(self._connect()) as conn:
            conn.execute('BEGIN IMMEDIATE')
            try:
                used=conn.execute("SELECT COUNT(*) FROM model_runs m LEFT JOIN model_run_scopes s ON s.job_id=m.job_id WHERE COALESCE(s.mission,'legacy')=?",(self.model_mission,)).fetchone()[0]
                if used >= 12:
                    raise StoreError(f'{self.model_mission} model çağrı bütçesi (12) doldu; otomatik tekrar yapılmaz.')
                if conn.execute('SELECT 1 FROM model_runs WHERE job_id=?',(job_id,)).fetchone():
                    raise StoreError('Bu model denemesi zaten kayıtlı; yeniden çalıştırılmadı.')
                conn.execute('INSERT INTO model_runs(job_id,kind,started_at) VALUES(?,?,?)',(job_id,kind,datetime.now(timezone.utc).isoformat()))
                conn.execute('INSERT INTO model_run_scopes(job_id,mission) VALUES(?,?)',(job_id,self.model_mission))
                conn.execute('COMMIT')
            except Exception:
                conn.execute('ROLLBACK')
                raise

    def finish_model_run(self, job_id: str, response_id: str | None, output_sha256: str | None, success: bool) -> None:
        now = datetime.now(timezone.utc)
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT started_at FROM model_runs WHERE job_id=?", (job_id,)).fetchone()
            started = datetime.fromisoformat(row["started_at"]) if row and row["started_at"] else now
            duration = max(0, int((now - started).total_seconds() * 1000))
            conn.execute("UPDATE model_runs SET completed_at=?,duration_ms=?,response_id=?,output_sha256=?,success=? WHERE job_id=?", (now.isoformat(), duration, response_id, output_sha256, int(success), job_id))

    def model_budget_summary(self) -> dict[str, Any]:
        with closing(self._connect()) as conn:
            pending = conn.execute("SELECT COUNT(*) FROM jobs WHERE kind IN ('generate','model_review') AND status='pending'").fetchone()[0]
            calls = [dict(row) for row in conn.execute("SELECT m.* FROM model_runs m LEFT JOIN model_run_scopes s ON s.job_id=m.job_id WHERE COALESCE(s.mission,'legacy')=? ORDER BY m.started_at,m.job_id",(self.model_mission,)).fetchall()]
            lifetime = conn.execute('SELECT COUNT(*) FROM model_runs').fetchone()[0]
        used=len(calls)
        return {"mission":self.model_mission,"lifetime_attempts":lifetime,"limit": 12, "used": used, "available": max(0, 12 - used), "pending": pending, "calls": calls, "total_duration_ms": sum(call.get("duration_ms") or 0 for call in calls)}

    def source_for_draft(self, draft_id: str) -> dict[str, Any]:
        draft = self.get_draft(draft_id)
        snapshot = draft.get('source_snapshot')
        if isinstance(snapshot, dict):
            source = json.loads(json.dumps(snapshot))
            source['snapshot_scope'] = 'frozen'
        else:
            # Missing historical bytes cannot be reconstructed from today's bookmark.
            evidence=draft.get('source_evidence') or {}
            source={'id':draft['source_id'],'url':draft.get('source_url',''),'title':'','text':'','author':'','observed_at':draft.get('observed_at',''),'evidence_note':draft.get('evidence_note',''),'verified_urls':[item['url'] for item in evidence.get('linked_primary_sources',[]) if isinstance(item,dict) and isinstance(item.get('url'),str)],'snapshot_scope':'legacy_partial'}
        source['source_evidence'] = draft.get('source_evidence')
        if source['snapshot_scope'] == 'legacy_partial':
            historical_refs=[item.get('url') for item in (draft.get('claim_map') or {}).get('evidence',[]) if isinstance(item,dict) and item.get('kind')=='source' and valid_source_url(item.get('url'))]
            source['verified_urls']=list(dict.fromkeys([*source['verified_urls'],*historical_refs]))
            source['reference_scope']='historical_claim_map_references_not_new_verification'
        snapshots = draft.get("project_sources")
        if source["snapshot_scope"] == "legacy_partial":
            # _decode_draft exposes a synthetic primary entry for old rows. Keep the
            # historically recorded claim URLs on that primary boundary and retain
            # only explicitly attached additional snapshots.
            extras = [item for item in (snapshots or []) if isinstance(item, dict) and item.get("id") != source.get("id")]
            source["project_sources"] = json.loads(json.dumps([source, *extras]))
        else:
            source["project_sources"] = json.loads(json.dumps(snapshots if isinstance(snapshots, list) and snapshots else [source]))
        return source

    @staticmethod
    def allowed_source_urls(source: dict[str, Any]) -> set[str]:
        """Return only URLs frozen into this content project's source boundary."""
        urls: set[str] = set()
        items = source.get("project_sources")
        if not isinstance(items, list) or not items:
            items = [source]
        for item in items:
            if not isinstance(item, dict):
                continue
            if valid_source_url(item.get("url")):
                urls.add(item["url"])
            for url in item.get("verified_urls", []):
                if valid_source_url(url):
                    urls.add(url)
        return urls

    @staticmethod
    def _brief_fingerprint(draft: dict[str, Any]) -> str:
        brief = normalize_editorial(draft)["reader_brief"]
        return hashlib.sha256(json.dumps(brief, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    def require_generation_loop_clear(self, draft_id: str, revision: int) -> None:
        draft = self.get_draft(draft_id)
        if draft["revision"] != revision:
            raise RevisionConflict("Taslak başka bir düzenleme ile değişti.")
        current_brief = self._brief_fingerprint(draft)
        failed_revisions = {
            item.get("revision") for item in draft["reviews"]["model"]
            if item.get("brief_fingerprint") == current_brief
            and item.get("recommended_action") in {"revise", "return_to_brief"}
            and isinstance(item.get("revision"), int)
        }
        if len(failed_revisions) >= 2:
            raise StoreError("Aynı brief iki farklı revizyonda model incelemesinden geçmedi; üçüncü üretimden önce brief veya açı gerçekten değiştirilmelidir.")

    def apply_generated_draft(self, draft_id: str, revision: int, generated: dict[str, Any], *, _proposal_id: str | None = None) -> dict[str, Any]:
        return self.update_draft(draft_id, revision, generated, _proposal_id=_proposal_id)

    def save_generated_proposal(self, job: dict[str, Any], generated: dict[str, Any]) -> None:
        with closing(self._connect()) as conn:
            draft = conn.execute('SELECT revision FROM drafts WHERE id=?', (job['draft_id'],)).fetchone()
            if not draft or draft['revision'] != job['revision']:
                raise RevisionConflict('Öneri hazırlanırken taslak değişti; metin korunuyor.')
            conn.execute('INSERT INTO proposals(id,draft_id,revision,data,status) VALUES(?,?,?,?,?)',
                         (job['id'],job['draft_id'],job['revision'],json.dumps(generated,ensure_ascii=False),'pending'))

    def list_proposals(self) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            return [{**dict(row),'data':json.loads(row['data'])} for row in conn.execute('SELECT * FROM proposals ORDER BY rowid')]

    def apply_proposal(self, draft_id: str, proposal_id: str, revision: int) -> dict[str, Any]:
        with closing(self._connect()) as conn:
            proposal = conn.execute('SELECT * FROM proposals WHERE id=? AND draft_id=?',(proposal_id,draft_id)).fetchone()
        if not proposal or proposal['status'] != 'pending' or type(revision) is not int or proposal['revision'] != revision:
            raise RevisionConflict('Öneri bu taslağın bekleyen revizyonuna ait değil; metin korunuyor.')
        return self.apply_generated_draft(draft_id,revision,json.loads(proposal['data']),_proposal_id=proposal_id)

    def discard_proposal(self, draft_id: str, proposal_id: str, revision: int) -> dict[str, str]:
        with closing(self._connect()) as conn:
            changed=conn.execute("UPDATE proposals SET status='discarded' WHERE id=? AND draft_id=? AND revision=? AND status='pending'",(proposal_id,draft_id,revision))
            if changed.rowcount != 1:
                raise RevisionConflict('Bekleyen öneri bulunamadı; taslak değişmedi.')
        return {'status':'discarded'}

    def require_editorial_ready(self, draft_id: str, revision: int, *, for_generation: bool = False) -> None:
        draft = self.get_draft(draft_id)
        if draft["revision"] != revision:
            raise RevisionConflict("Taslak başka bir düzenleme ile değişti.")
        source = self.source_for_draft(draft_id)
        try:
            brief = validate_reader_brief(draft["reader_brief"], allowed_angles=suggest_angles(draft, source, draft["reader_brief"]))
            if brief['selected_angle'] is None:
                raise EditorialError('Önce bir içerik açısı seçilmelidir.')
            if draft["claim_map"] is None:
                if for_generation and brief['provenance'] != 'tested_external':
                    return  # The first draft's claims cannot exist before its text.
                raise EditorialError("İnsan onayı ve deney üretimi için iddia haritası zorunludur.")
            validate_claim_map(draft["claim_map"], brief, draft.get("assets", []), allowed_urls=self.allowed_source_urls(source))
        except EditorialError as exc:
            raise StoreError("Editoryal brief henüz üretim/onay için hazır değil: " + str(exc)) from exc

    def list_angles(self, draft_id: str) -> dict[str, Any]:
        draft = self.get_draft(draft_id)
        return {"draft_id": draft_id, "revision": draft["revision"], "angles": suggest_angles(draft, self.source_for_draft(draft_id), draft["reader_brief"])}

    def save_deterministic_review(self, draft_id: str, revision: int) -> dict[str, Any]:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
            if row is None:
                raise StoreError("Taslak bulunamadı.")
            if row["revision"] != revision:
                raise RevisionConflict("Taslak başka bir düzenleme ile değişti.")
            draft = self._decode_draft(row)
            editorial = normalize_editorial(draft)
            fingerprint=approval_fingerprint(draft)
            brief_fingerprint=self._brief_fingerprint(draft)
            prior=editorial['reviews']['deterministic']
            same=next((item for item in reversed(prior) if item.get('revision')==revision and item.get('approval_fingerprint')==fingerprint),None)
            if same is not None:return same
            failed={item.get('revision') for item in prior if item.get('brief_fingerprint')==brief_fingerprint and not item.get('passed') and item.get('revision')!=revision}
            result = {**deterministic_review(draft, len(failed)), "revision": revision, "approval_fingerprint":fingerprint,"brief_fingerprint":brief_fingerprint}
            editorial["reviews"]["deterministic"] = (editorial["reviews"]["deterministic"] + [result])[-20:]
            editorial["audit"] = {"at": datetime.now(timezone.utc).isoformat(), "revision": revision, "approval_fingerprint": approval_fingerprint(draft), "deterministic": result, "semantic_truth_note": result["semantic_truth_note"]}
            draft["editorial"] = editorial
            self._store_editorial(draft)
            if conn.execute("UPDATE drafts SET data=? WHERE id=? AND revision=? AND data=?", (json.dumps(draft, ensure_ascii=False), draft_id, revision, row["data"])).rowcount != 1:
                raise RevisionConflict("İnceleme sırasında taslak değişti.")
        return result

    def save_model_review(self, draft_id: str, revision: int, review: dict[str, Any], *, expected_fingerprint: str | None = None) -> None:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
            if row is None or row["revision"] != revision:
                raise RevisionConflict("Taslak başka bir düzenleme ile değişti.")
            draft = self._decode_draft(row)
            editorial = normalize_editorial(draft)
            if expected_fingerprint is not None and approval_fingerprint(draft) != expected_fingerprint:
                raise RevisionConflict('İnceleme sırasında kanıt veya medya değişti; geç eleştiri kaydedilmedi.')
            saved_review = {**review, "at": datetime.now(timezone.utc).isoformat(), "revision": revision, "brief_fingerprint": self._brief_fingerprint(draft), "approval_fingerprint": approval_fingerprint(draft)}
            editorial["reviews"]["model"] = (editorial["reviews"]["model"] + [saved_review])[-20:]
            draft["editorial"] = editorial
            self._store_editorial(draft)
            if conn.execute("UPDATE drafts SET data=? WHERE id=? AND revision=? AND data=?", (json.dumps(draft, ensure_ascii=False), draft_id, revision, row["data"])).rowcount != 1:
                raise RevisionConflict("Model eleştirisi kaydedilirken taslak değişti.")

    def editorial_reviews(self, draft_id: str) -> dict[str, Any]:
        draft = self.get_draft(draft_id)
        return {"revision": draft["revision"], "deterministic": draft["reviews"]["deterministic"], "model": draft["reviews"]["model"], "audit": draft["audit"]}

    def register_media(self, draft_id: str, revision: int, out_dir: Path, result: dict[str, Any]) -> dict[str, Any]:
        with closing(self._connect()) as conn:
            row = conn.execute('SELECT * FROM drafts WHERE id = ?', (draft_id,)).fetchone()
        if row is None:
            raise StoreError('Taslak bulunamadı.')
        draft = self._decode_draft(row)
        if draft["revision"] != revision:
            raise RevisionConflict("Taslak başka bir düzenleme ile değişti.")
        if not isinstance(result, dict) or not isinstance(result.get("files"), list):
            raise StoreError("Medya işçisi geçerli varlık listesi vermedi.")
        root = out_dir.resolve()
        warnings = result.get("warnings", [])
        alt_text = result.get("alt_text", "")
        rights = result.get("rights")
        provenance = result.get("provenance")
        source_filename = result.get("source_filename")
        if not isinstance(warnings, list) or not all(isinstance(item, str) and len(item) <= 1_000 for item in warnings):
            raise StoreError("Medya uyarıları geçersiz.")
        if not isinstance(alt_text, str) or len(alt_text) > 1_000:
            raise StoreError("Medya alt metni geçersiz.")
        if rights is not None and rights not in {"owned", "licensed"}:
            raise StoreError("Medya hak beyanı geçersiz.")
        if provenance is not None and (not isinstance(provenance, str) or len(provenance) > 100):
            raise StoreError("Medya köken bilgisi geçersiz.")
        if source_filename is not None and not self._safe_name(source_filename):
            raise StoreError("Kaynak dosya adı güvenli değil.")
        file_infos: list[dict[str, Any]] = []
        for file_info in result["files"]:
            if not isinstance(file_info, dict):
                raise StoreError("Medya varlık bildirimi geçersiz.")
            name, kind, mime = file_info.get("name"), file_info.get("kind"), file_info.get("mime")
            if not all(isinstance(value, str) and value for value in (name, kind, mime)) or not self._safe_name(name):
                raise StoreError("Medya varlık adı güvenli değil.")
            path = (root / name).resolve()
            if path.parent != root or not path.is_file() or path.stat().st_size <= 0:
                raise StoreError("Medya işçisi bildirilen gerçek varlığı üretmedi.")
            file_infos.append({"name": name, "kind": kind, "mime": mime, "path": path, "for_asset": file_info.get("for_asset")})
        media_names = {
            asset.get("name") for asset in draft.get("assets", [])
            if asset.get("revision") == revision and asset.get("kind") in {"audio", "video"}
        }
        media_names.update(item["name"] for item in file_infos if item["kind"] in {"audio", "video"})
        legacy_bindings = {"captions.tr.vtt": "video.mp4", "voice.tr.vtt": "voice.mp3"}
        for item in file_infos:
            if item["kind"] != "subtitle":
                continue
            target = item["for_asset"]
            if target is None:
                target = legacy_bindings.get(item["name"])
            if not self._safe_name(target):
                raise StoreError("Altyazının bağlı medya adı güvenli değil.")
            if target not in media_names:
                raise StoreError("Altyazının bağlı medyası bu taslağın geçerli yerel ses veya videosu olmalıdır.")
            item["for_asset"] = target
        assets: list[dict[str, Any]] = []
        for item in file_infos:
            path = item["path"]
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            assets.append({"name": item["name"], "kind": item["kind"], "mime": item["mime"], "revision": revision,
                           "sha256": digest, "size": path.stat().st_size,
                           "storage_name": f"{draft_id}/r{revision}/{item['name']}", "rights": rights,
                           "provenance": provenance, "source_filename": source_filename,
                           "for_asset": item["for_asset"] if item["kind"] == "subtitle" else None,
                           "alt_text": alt_text, "warnings": warnings})
        draft["assets"] = [asset for asset in draft["assets"] if not (asset.get("revision") == revision and asset.get("name") in {new["name"] for new in assets})] + assets
        primary = draft.get("primary_media")
        if primary and not any(asset.get("name") == primary.get("name") and asset.get("revision") == primary.get("revision") and asset.get("sha256") == primary.get("sha256") for asset in draft["assets"]):
            draft["primary_media"] = None
        self._clear_approvals(draft)
        with closing(self._connect()) as conn:
            changed = conn.execute("UPDATE drafts SET data = ? WHERE id = ? AND revision = ? AND data = ?", (json.dumps(draft, ensure_ascii=False), draft_id, revision, row['data']))
            if changed.rowcount != 1:
                raise RevisionConflict('Medya hazırlanırken taslak değişti.')
        return draft

    def get_asset(self, draft_id: str, filename: str, revision: int | None = None, sha256: str | None = None) -> dict[str, Any]:
        if not self._safe_name(filename):
            raise StoreError("Varlık adı güvenli değil.")
        draft = self.get_draft(draft_id)
        for asset in reversed(draft["assets"]):
            if asset.get("name") == filename:
                if revision is not None and asset.get("revision") != revision:
                    continue
                if sha256 is not None and asset.get("sha256") != sha256:
                    continue
                return asset
        raise StoreError("Varlık bulunamadı.")

    @staticmethod
    def _safe_name(name: Any) -> bool:
        return (
            isinstance(name, str) and bool(name) and len(name) <= 255
            and name not in {".", ".."} and "/" not in name and "\\" not in name and ":" not in name
            and Path(name).name == name
        )
