from __future__ import annotations

import ipaddress
import socket
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field

from app.services.adapters import get_adapters
from app.services.tools.base import PermissionLevel, Tool, ToolContext, ToolPermissionDenied, ToolTransientError, register


class WebSearchInput(BaseModel):
    query: str = Field(min_length=2, max_length=400)
    industries: list[str] = Field(default_factory=list, max_length=20)
    country: str | None = None
    min_employees: int | None = Field(default=None, ge=0)
    max_employees: int | None = Field(default=None, ge=0)
    result_type: Literal["company", "web", "any"] = "any"
    kind: str | None = None
    vendor: str | None = None
    limit: int = Field(default=10, ge=1, le=50)
    offset: int = Field(default=0, ge=0, le=500)


@register
class WebSearchTool(Tool):
    name = "web_search"
    description = "Search the web / company directories. Returns titles, URLs and snippets (untrusted content)."
    category = "research"
    permission_level = PermissionLevel.READ
    input_model = WebSearchInput
    output_description = {"results": "list[{title,url,snippet,domain?,industry?}]"}
    timeout_seconds = 10.0
    untrusted_output = True

    async def run(self, ctx: ToolContext, args: WebSearchInput) -> dict[str, Any]:
        filters: dict[str, Any] = {"industries": args.industries, "country": args.country,
                                   "min_employees": args.min_employees, "max_employees": args.max_employees,
                                   "kind": args.kind, "vendor": args.vendor}
        if args.result_type != "any":
            filters["type"] = args.result_type
        results = await get_adapters().search.search(args.query, filters, args.limit, args.offset)
        return {"query": args.query, "count": len(results), "results": results}


class CompanyLookupInput(BaseModel):
    domains: list[str] = Field(min_length=1, max_length=40)


@register
class CompanyLookupTool(Tool):
    name = "company_lookup"
    description = "Firmographic enrichment for company domains: industry, size, locations, technology and security signals with confidence."
    category = "research"
    input_model = CompanyLookupInput
    timeout_seconds = 15.0

    async def run(self, ctx: ToolContext, args: CompanyLookupInput) -> dict[str, Any]:
        return {"companies": await get_adapters().company.lookup(args.domains)}


class RegistryInput(BaseModel):
    domain: str = Field(min_length=3, max_length=200)


@register
class CompanyRegistryTool(Tool):
    name = "company_registry_lookup"
    description = "Official company registry record (registered name, incorporation, reported headcount)."
    category = "research"
    input_model = RegistryInput

    async def run(self, ctx: ToolContext, args: RegistryInput) -> dict[str, Any]:
        rec = await get_adapters().company.registry(args.domain)
        return {"found": rec is not None, "record": rec}


class PeopleSearchInput(BaseModel):
    domain: str | None = Field(default=None, min_length=3, max_length=200)
    domains: list[str] = Field(default_factory=list, max_length=50)
    roles: list[str] = Field(default_factory=list, max_length=10)


@register
class PeopleSearchTool(Tool):
    name = "people_search"
    description = "Find publicly listed leadership at a company, optionally filtered by role keywords."
    category = "research"
    input_model = PeopleSearchInput

    async def run(self, ctx: ToolContext, args: PeopleSearchInput) -> dict[str, Any]:
        domains = ([args.domain] if args.domain else []) + args.domains
        provider = get_adapters().company
        return {"results": [{"domain": d, "people": await provider.people(d, args.roles)} for d in domains]}


class HttpRequestInput(BaseModel):
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "GET"
    url: str = Field(max_length=2000)
    headers: dict[str, str] = Field(default_factory=dict)
    json_body: dict[str, Any] | None = None


def assert_public_url(url: str, allowlist: list[str]) -> None:
    """SSRF protection: https only, host allowlist, and no private/loopback/link-local targets."""
    p = urlparse(url)
    if p.scheme != "https" or not p.hostname:
        raise ToolPermissionDenied("only https URLs are allowed")
    host = p.hostname.lower()
    if allowlist and not any(host == a or host.endswith("." + a) for a in allowlist):
        raise ToolPermissionDenied(f"host '{host}' is not in the tool allowlist")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise ToolTransientError(f"DNS resolution failed for {host}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ToolPermissionDenied(f"host '{host}' resolves to a non-public address")


@register
class HttpRequestTool(Tool):
    name = "http_request"
    description = "Call an allow-listed external HTTPS API. Response bodies are untrusted."
    category = "integration"
    permission_level = PermissionLevel.EXTERNAL
    input_model = HttpRequestInput
    timeout_seconds = 20.0
    sensitive_args = ("url",)
    untrusted_output = True

    def facts(self, args: HttpRequestInput) -> dict[str, Any]:
        return {"method": args.method, "host": urlparse(args.url).hostname or ""}

    async def run(self, ctx: ToolContext, args: HttpRequestInput) -> dict[str, Any]:
        assert_public_url(args.url, ctx.config.get("allowlist", []))
        headers = {k: v for k, v in args.headers.items() if k.lower() not in ("host", "cookie", "authorization")}
        async with httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=False) as client:
            r = await client.request(args.method, args.url, headers=headers, json=args.json_body)
        if r.status_code >= 500:
            raise ToolTransientError(f"upstream {r.status_code}")
        body: Any
        try:
            body = r.json()
        except ValueError:
            body = r.text[:20000]
        return {"status": r.status_code, "body": body}
