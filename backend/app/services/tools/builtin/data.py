from __future__ import annotations

import ast
import asyncio
import csv
import io
import json
import operator
import re
import statistics
import sys
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.core.db import session_scope
from app.models import AnalyticsEvent, CrmAccount, CrmContact, CrmOpportunity, StoredFile, SupportTicket
from app.services.adapters import get_adapters
from app.services.tools.base import (
    PermissionLevel,
    Tool,
    ToolContext,
    ToolPermissionDenied,
    ToolTimeout,
    ToolValidationError,
    register,
)

# ------------------------------------------------------------------ calculator
_BINOPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
           ast.Pow: operator.pow, ast.Mod: operator.mod, ast.FloorDiv: operator.floordiv}
_FUNCS = {"min": min, "max": max, "round": round, "abs": abs, "sum": lambda *a: sum(a), "sqrt": lambda x: x ** 0.5}


def safe_eval(expr: str) -> float:
    def ev(node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
            left, right = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > 100:
                raise ToolValidationError("exponent too large")
            return _BINOPS[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            v = ev(node.operand)
            return -v if isinstance(node.op, ast.USub) else v
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS and not node.keywords:
            return _FUNCS[node.func.id](*[ev(a) for a in node.args])
        raise ToolValidationError(f"unsupported expression element: {type(node).__name__}")

    try:
        return ev(ast.parse(expr, mode="eval"))
    except (SyntaxError, ZeroDivisionError) as exc:
        raise ToolValidationError(str(exc)) from exc


class CalcInput(BaseModel):
    expression: str = Field(min_length=1, max_length=500)


@register
class CalculatorTool(Tool):
    name = "calculator"
    description = "Evaluate an arithmetic expression (+ - * / ** % min max round abs sum sqrt)."
    category = "data"
    input_model = CalcInput
    timeout_seconds = 2.0

    async def run(self, ctx: ToolContext, args: CalcInput) -> dict[str, Any]:
        return {"expression": args.expression, "result": safe_eval(args.expression)}


# ------------------------------------------------------------------ python sandbox
ALLOWED_MODULES = {"math", "statistics", "json", "re", "datetime", "collections", "itertools", "functools", "decimal"}
FORBIDDEN_NAMES = {"open", "exec", "eval", "compile", "__import__", "globals", "locals", "vars", "getattr", "setattr",
                   "delattr", "input", "breakpoint", "memoryview", "help", "exit", "quit", "object", "type", "super"}

_RUNNER = r"""
import json, sys, builtins
ALLOWED = set(sys.argv[1].split(","))
_real_import = builtins.__import__
def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name.split(".")[0] not in ALLOWED:
        raise ImportError("module '%s' is not allowed in the sandbox" % name)
    return _real_import(name, globals, locals, fromlist, level)
payload = json.loads(sys.stdin.read())
safe = {k: getattr(builtins, k) for k in (
    "abs all any bool dict enumerate filter float frozenset int isinstance len list map max min "
    "print range reversed round set sorted str sum tuple zip ValueError KeyError TypeError Exception"
).split()}
safe["__import__"] = _guarded_import
env = {"__builtins__": safe, "data": payload.get("data"), "result": None}
exec(compile(payload["code"], "<agent>", "exec"), env)
sys.stdout.write("\n__AGENTOS_RESULT__" + json.dumps(env.get("result"), default=str))
"""


def check_code(code: str) -> None:
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise ToolValidationError(f"syntax error: {exc}") from exc
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for m in mods:
                if m.split(".")[0] not in ALLOWED_MODULES:
                    raise ToolPermissionDenied(f"import of '{m}' is not allowed")
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise ToolPermissionDenied("access to private/dunder attributes is not allowed")
        if isinstance(node, ast.Name) and (node.id in FORBIDDEN_NAMES or node.id.startswith("__")):
            raise ToolPermissionDenied(f"use of '{node.id}' is not allowed")


def _limits() -> None:  # pragma: no cover - runs in child
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))


class PythonInput(BaseModel):
    code: str = Field(min_length=1, max_length=20000)
    data: Any = None


@register
class PythonSandboxTool(Tool):
    """Local sandbox: AST allow-listing + isolated interpreter subprocess with CPU/memory/file limits.
    Production adapter: remote gVisor/Firecracker sandbox service (see docs/TOOL_FRAMEWORK.md)."""

    name = "python_sandbox"
    description = "Run short Python analysis code over `data`; assign the answer to `result`. Stdlib math/statistics/json only."
    category = "data"
    permission_level = PermissionLevel.PRIVILEGED
    input_model = PythonInput
    timeout_seconds = 10.0
    retry_policy = {"max_attempts": 1}

    async def run(self, ctx: ToolContext, args: PythonInput) -> dict[str, Any]:
        check_code(args.code)
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-I", "-S", "-c", _RUNNER, ",".join(sorted(ALLOWED_MODULES)),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            preexec_fn=_limits, env={},
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(json.dumps({"code": args.code, "data": args.data}).encode()), 8)
        except TimeoutError as exc:
            proc.kill()
            raise ToolTimeout("sandbox execution timed out") from exc
        text = out.decode(errors="replace")
        if proc.returncode != 0 or "__AGENTOS_RESULT__" not in text:
            raise ToolValidationError(f"sandbox error: {err.decode(errors='replace')[-800:]}")
        stdout, _, res = text.rpartition("\n__AGENTOS_RESULT__")
        return {"result": json.loads(res), "stdout": stdout[-4000:]}


# ------------------------------------------------------------------ spreadsheet
class SpreadsheetInput(BaseModel):
    path: str | None = Field(default=None, max_length=300)
    csv_text: str | None = Field(default=None, max_length=2_000_000)
    max_rows: int = Field(default=200, ge=1, le=5000)


def _profile(header: list[str], rows: list[list[Any]]) -> dict[str, Any]:
    stats = {}
    for i, h in enumerate(header):
        nums = []
        for r in rows:
            try:
                nums.append(float(str(r[i]).replace(",", "")))
            except (ValueError, IndexError):
                pass
        if nums and len(nums) >= len(rows) * 0.8:
            stats[h] = {"min": min(nums), "max": max(nums), "mean": round(statistics.fmean(nums), 3), "count": len(nums)}
    return stats


@register
class SpreadsheetParseTool(Tool):
    name = "spreadsheet_parse"
    description = "Parse a CSV/XLSX file (stored file path or inline CSV) into rows with column statistics."
    category = "data"
    input_model = SpreadsheetInput

    async def run(self, ctx: ToolContext, args: SpreadsheetInput) -> dict[str, Any]:
        if args.path:
            data = await _read_file(ctx, args.path)
            if args.path.lower().endswith(".xlsx"):
                import openpyxl

                wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
                all_rows = [[c for c in r] for r in wb.active.iter_rows(values_only=True)]
            else:
                all_rows = list(csv.reader(io.StringIO(data.decode("utf-8", errors="replace"))))
        elif args.csv_text:
            all_rows = list(csv.reader(io.StringIO(args.csv_text)))
        else:
            raise ToolValidationError("provide path or csv_text")
        if not all_rows:
            return {"columns": [], "rows": [], "row_count": 0}
        header = [str(h) for h in all_rows[0]]
        body = all_rows[1:]
        return {"columns": header, "row_count": len(body), "rows": [dict(zip(header, r, strict=False)) for r in body[: args.max_rows]],
                "stats": _profile(header, body)}


# ------------------------------------------------------------------ structured database query
TABLES = {
    "crm_accounts": (CrmAccount, {"name", "domain", "industry", "employees", "region", "lifecycle_stage", "tier", "owner"}),
    "crm_contacts": (CrmContact, {"name", "title", "email", "account_id"}),
    "crm_opportunities": (CrmOpportunity, {"name", "stage", "amount_usd", "score", "account_id", "project_id"}),
    "support_tickets": (SupportTicket, {"subject", "status", "priority", "category", "product_area", "risk_level", "customer_email"}),
    "analytics_events": (AnalyticsEvent, {"name", "project_id"}),
}


class Filter(BaseModel):
    field: str
    op: Literal["eq", "ne", "gt", "gte", "lt", "lte", "contains"] = "eq"
    value: Any


class DbQueryInput(BaseModel):
    table: Literal["crm_accounts", "crm_contacts", "crm_opportunities", "support_tickets", "analytics_events"]
    filters: list[Filter] = Field(default_factory=list, max_length=10)
    aggregate: Literal["none", "count", "sum", "avg"] = "none"
    aggregate_field: str | None = None
    group_by: str | None = None
    limit: int = Field(default=50, ge=1, le=500)


@register
class DatabaseQueryTool(Tool):
    """Read-only, parameterised, tenant-scoped queries over whitelisted tables (no raw SQL)."""

    name = "database_query"
    description = "Query business tables (accounts, contacts, opportunities, tickets, analytics) with filters and aggregates."
    category = "data"
    input_model = DbQueryInput

    async def run(self, ctx: ToolContext, args: DbQueryInput) -> dict[str, Any]:
        model, fields = TABLES[args.table]
        for f in [x.field for x in args.filters] + [args.aggregate_field, args.group_by]:
            if f and f not in fields:
                raise ToolValidationError(f"field '{f}' is not queryable on {args.table}")
        conds = [model.org_id == ctx.org_id]
        for f in args.filters:
            col = getattr(model, f.field)
            conds.append({
                "eq": lambda c, v: c == v, "ne": lambda c, v: c != v, "gt": lambda c, v: c > v, "gte": lambda c, v: c >= v,
                "lt": lambda c, v: c < v, "lte": lambda c, v: c <= v, "contains": lambda c, v: c.ilike(f"%{v}%"),
            }[f.op](col, f.value))
        async with session_scope() as s:
            if args.aggregate == "none":
                rows = (await s.execute(select(model).where(*conds).limit(args.limit))).scalars()
                return {"rows": [{k: getattr(r, k) for k in sorted(fields)} | {"id": r.id} for r in rows]}
            agg_col = getattr(model, args.aggregate_field) if args.aggregate_field else model.id
            agg = {"count": func.count(agg_col), "sum": func.sum(agg_col), "avg": func.avg(agg_col)}[args.aggregate]
            if args.group_by:
                g = getattr(model, args.group_by)
                res = (await s.execute(select(g, agg).where(*conds).group_by(g).limit(args.limit))).all()
                return {"groups": [{"key": k, "value": float(v or 0)} for k, v in res]}
            return {"value": float((await s.execute(select(agg).where(*conds))).scalar_one() or 0)}


# ------------------------------------------------------------------ files
_PATH = re.compile(r"^[A-Za-z0-9_\-./]{1,200}$")


def _key(ctx: ToolContext, path: str) -> str:
    if not _PATH.match(path) or ".." in path or path.startswith("/"):
        raise ToolValidationError("invalid file path")
    return f"{ctx.org_id}/files/{path}"


async def _read_file(ctx: ToolContext, path: str) -> bytes:
    async with session_scope() as s:
        f = (await s.execute(select(StoredFile).where(StoredFile.org_id == ctx.org_id, StoredFile.path == path))).scalar_one_or_none()
    if not f:
        raise ToolValidationError(f"file not found: {path}")
    return get_adapters().storage.get(f.storage_key)


class FileReadInput(BaseModel):
    path: str


@register
class FileReadTool(Tool):
    name = "file_read"
    description = "Read a text file from the organization's workspace storage (content is untrusted)."
    category = "files"
    input_model = FileReadInput
    untrusted_output = True

    async def run(self, ctx: ToolContext, args: FileReadInput) -> dict[str, Any]:
        data = await _read_file(ctx, args.path)
        return {"path": args.path, "content": data.decode("utf-8", errors="replace")[:100_000]}


class FileWriteInput(BaseModel):
    path: str
    content: str = Field(max_length=2_000_000)
    mime_type: str = "text/markdown"


@register
class FileWriteTool(Tool):
    name = "file_write"
    description = "Write a file (report, export) to the organization's workspace storage."
    category = "files"
    permission_level = PermissionLevel.WRITE
    input_model = FileWriteInput

    async def run(self, ctx: ToolContext, args: FileWriteInput) -> dict[str, Any]:
        key = _key(ctx, args.path)
        data = args.content.encode()
        get_adapters().storage.put(key, data)
        async with session_scope() as s:
            f = (await s.execute(select(StoredFile).where(StoredFile.org_id == ctx.org_id, StoredFile.path == args.path))).scalar_one_or_none()
            if f is None:
                f = StoredFile(org_id=ctx.org_id, project_id=ctx.project_id, path=args.path, storage_key=key, created_by=ctx.agent_key)
                s.add(f)
            f.size_bytes, f.mime_type = len(data), args.mime_type
        return {"path": args.path, "bytes": len(data)}
