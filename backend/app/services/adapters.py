"""Integration adapters.

Each integration has an interface, a local implementation (used in development, tests and the
offline demo) and a production implementation selected when credentials are configured.
Tools talk only to these interfaces, never to vendors directly.
"""

from __future__ import annotations

import math
import os
import re
import smtplib
from abc import ABC, abstractmethod
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import httpx

from app.core.config import get_settings
from app.seed.datasets import company_directory, web_corpus

_WORD = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return _WORD.findall(text.lower())


# ---------------------------------------------------------------- search
class SearchProvider(ABC):
    @abstractmethod
    async def search(self, query: str, filters: dict[str, Any], limit: int, offset: int) -> list[dict]: ...


class LocalSearchProvider(SearchProvider):
    """Ranks the local company directory and web corpus with TF-IDF over title/description."""

    def __init__(self) -> None:
        self.docs: list[dict] = []
        for c in company_directory():
            self.docs.append({
                "type": "company", "title": c["name"], "url": c["url"], "domain": c["domain"],
                "snippet": c["description"], "industry": c["industry"], "country": c["country"],
                "region": c["region"], "employees_hint": c["employees"],
                "_text": f"{c['name']} {c['industry']} {c['description']} {c['region']}",
            })
        for d in web_corpus():
            self.docs.append({"type": "web", "title": d["title"], "url": d["url"], "snippet": d["text"],
                              "kind": d["kind"], "vendor": d.get("vendor"), "_text": f"{d['title']} {d['text']}"})
        df: dict[str, int] = {}
        self._doc_tokens = []
        for d in self.docs:
            toks = set(_tokens(d["_text"]))
            self._doc_tokens.append(toks)
            for t in toks:
                df[t] = df.get(t, 0) + 1
        n = len(self.docs)
        self.idf = {t: math.log((n + 1) / (c + 0.5)) for t, c in df.items()}

    async def search(self, query: str, filters: dict[str, Any], limit: int = 10, offset: int = 0) -> list[dict]:
        q = set(_tokens(query))
        results = []
        for d, toks in zip(self.docs, self._doc_tokens, strict=True):
            if filters.get("type") and d["type"] != filters["type"]:
                continue
            if filters.get("industries") and d.get("industry") not in filters["industries"]:
                continue
            if filters.get("country") and d.get("country") and d["country"] != filters["country"]:
                continue
            if filters.get("kind") and d.get("kind") != filters["kind"]:
                continue
            if filters.get("vendor") and d.get("vendor") != filters["vendor"]:
                continue
            emp = d.get("employees_hint")
            if emp is not None and filters.get("min_employees") and emp < filters["min_employees"] * 0.8:
                continue
            if emp is not None and filters.get("max_employees") and emp > filters["max_employees"] * 1.2:
                continue
            score = sum(self.idf.get(t, 0) for t in q & toks)
            if score > 0 or not q:
                results.append((score, d))
        results.sort(key=lambda x: (-x[0], x[1]["title"]))
        out = []
        for score, d in results[offset: offset + limit]:
            item = {k: v for k, v in d.items() if not k.startswith("_") and k != "employees_hint"}
            item["relevance"] = round(score, 3)
            out.append(item)
        return out


class SerperSearchProvider(SearchProvider):
    """Production web search via serper.dev (Google results)."""

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def search(self, query: str, filters: dict[str, Any], limit: int = 10, offset: int = 0) -> list[dict]:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post("https://google.serper.dev/search", headers={"X-API-KEY": self.api_key},
                                  json={"q": query, "num": limit, "page": offset // max(limit, 1) + 1, "gl": "uk"})
            r.raise_for_status()
        return [{"type": "web", "title": o.get("title"), "url": o.get("link"), "snippet": o.get("snippet")}
                for o in r.json().get("organic", [])]


# ---------------------------------------------------------------- company data
class CompanyDataProvider(ABC):
    @abstractmethod
    async def lookup(self, domains: list[str]) -> list[dict]: ...

    @abstractmethod
    async def registry(self, domain: str) -> dict | None: ...

    @abstractmethod
    async def people(self, domain: str, roles: list[str]) -> list[dict]: ...


class LocalCompanyDataProvider(CompanyDataProvider):
    def __init__(self) -> None:
        self.by_domain = {c["domain"]: c for c in company_directory()}

    async def lookup(self, domains: list[str]) -> list[dict]:
        out = []
        for d in domains:
            c = self.by_domain.get(d)
            if not c:
                out.append({"domain": d, "found": False})
                continue
            out.append({
                "found": True, "domain": d, "name": c["name"], "industry": c["industry"],
                "employees": c["employees"], "country": c["country"], "region": c["region"],
                "hq_city": c["hq_city"], "uk_sites": c["uk_sites"], "revenue_band": c["revenue_band"],
                "tech_indicators": c["tech_indicators"], "security_signals": c["security_signals"],
                "source_url": c["url"],
            })
        return out

    async def registry(self, domain: str) -> dict | None:
        c = self.by_domain.get(domain)
        if not c:
            return None
        return {"domain": domain, "registered_name": c["name"], "employees": c["registry_employees"],
                "incorporated": c["founded"], "source_url": f"https://registry.example/company/{c['id']}"}

    async def people(self, domain: str, roles: list[str]) -> list[dict]:
        c = self.by_domain.get(domain)
        if not c:
            return []
        wanted = [r.lower() for r in roles]
        people = c["people"]
        if wanted:
            people = [p for p in people if any(w in p["title"].lower() for w in wanted)] or people
        return [dict(p, source_url=f"{c['url']}/about/leadership") for p in people]


class CompaniesHouseProvider(LocalCompanyDataProvider):
    """Production registry adapter (UK Companies House REST API). Firmographic enrichment and
    people search fall back to the configured enrichment vendor; here we override registry()."""

    def __init__(self, api_key: str):
        super().__init__()
        self.api_key = api_key

    async def registry(self, domain: str) -> dict | None:
        name = domain.split(".")[0]
        async with httpx.AsyncClient(timeout=15, auth=(self.api_key, "")) as client:
            r = await client.get("https://api.company-information.service.gov.uk/search/companies", params={"q": name})
            r.raise_for_status()
        items = r.json().get("items", [])
        if not items:
            return None
        top = items[0]
        return {"domain": domain, "registered_name": top.get("title"), "employees": None,
                "incorporated": top.get("date_of_creation"),
                "source_url": "https://find-and-update.company-information.service.gov.uk" + top.get("links", {}).get("self", "")}


# ---------------------------------------------------------------- messaging
class EmailSender(ABC):
    @abstractmethod
    async def send(self, to: str, subject: str, body: str, meta: dict) -> dict: ...


class OutboxEmailSender(EmailSender):
    """Local delivery: messages are persisted to the outbound_messages table (status=sent)."""

    async def send(self, to: str, subject: str, body: str, meta: dict) -> dict:
        return {"delivered": True, "transport": "outbox"}


class SmtpEmailSender(EmailSender):
    def __init__(self, host: str, port: int, user: str, password: str, sender: str):
        self.host, self.port, self.user, self.password, self.sender = host, port, user, password, sender

    async def send(self, to: str, subject: str, body: str, meta: dict) -> dict:
        import asyncio

        def _send() -> None:
            msg = EmailMessage()
            msg["From"], msg["To"], msg["Subject"] = self.sender, to, subject
            msg.set_content(body)
            with smtplib.SMTP(self.host, self.port, timeout=20) as s:
                s.starttls()
                s.login(self.user, self.password)
                s.send_message(msg)

        await asyncio.to_thread(_send)
        return {"delivered": True, "transport": "smtp"}


class SlackSender(ABC):
    @abstractmethod
    async def post(self, channel: str, text: str) -> dict: ...


class OutboxSlackSender(SlackSender):
    async def post(self, channel: str, text: str) -> dict:
        return {"delivered": True, "transport": "outbox"}


class SlackWebhookSender(SlackSender):
    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    async def post(self, channel: str, text: str) -> dict:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(self.webhook_url, json={"channel": channel, "text": text})
            r.raise_for_status()
        return {"delivered": True, "transport": "slack"}


# ---------------------------------------------------------------- object storage
class ObjectStorage(ABC):
    @abstractmethod
    def put(self, key: str, data: bytes) -> str: ...

    @abstractmethod
    def get(self, key: str) -> bytes: ...


class LocalObjectStorage(ObjectStorage):
    def __init__(self, root: str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if not str(p).startswith(str(self.root) + os.sep):
            raise ValueError("invalid storage key")
        return p

    def put(self, key: str, data: bytes) -> str:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return key

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()


class S3ObjectStorage(ObjectStorage):
    def __init__(self, bucket: str):
        import boto3  # optional dependency in production image

        self.bucket, self.client = bucket, boto3.client("s3")

    def put(self, key: str, data: bytes) -> str:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ServerSideEncryption="aws:kms")
        return key

    def get(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()


# ---------------------------------------------------------------- factory
class Adapters:
    def __init__(self) -> None:
        s = get_settings()
        self.search: SearchProvider = (
            SerperSearchProvider(os.environ["SERPER_API_KEY"]) if os.environ.get("SERPER_API_KEY") else LocalSearchProvider()
        )
        self.company: CompanyDataProvider = (
            CompaniesHouseProvider(os.environ["COMPANIES_HOUSE_API_KEY"])
            if os.environ.get("COMPANIES_HOUSE_API_KEY") else LocalCompanyDataProvider()
        )
        if os.environ.get("SMTP_HOST"):
            self.email: EmailSender = SmtpEmailSender(
                os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "587")), os.environ.get("SMTP_USER", ""),
                os.environ.get("SMTP_PASSWORD", ""), os.environ.get("SMTP_FROM", "agentos@localhost"))
        else:
            self.email = OutboxEmailSender()
        self.slack: SlackSender = (
            SlackWebhookSender(os.environ["SLACK_WEBHOOK_URL"]) if os.environ.get("SLACK_WEBHOOK_URL") else OutboxSlackSender()
        )
        self.storage: ObjectStorage = S3ObjectStorage(s.s3_bucket) if s.s3_bucket else LocalObjectStorage(s.object_storage_path)


_adapters: Adapters | None = None


def get_adapters() -> Adapters:
    global _adapters
    if _adapters is None:
        _adapters = Adapters()
    return _adapters
